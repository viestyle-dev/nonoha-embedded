"""Runtime module for nonoha-embedded.

Provides the main Runtime class and TransportStrategy protocol.
Includes logging configuration (integrated, no separate logging.py).

Design notes:
- Runtime is dart_bridge independent
- TransportStrategy is defined here (file count reduction)
- TcpServerStrategy is in tcp_server_strategy.py (lazy import when not using TCP)
- Uses asyncio.new_event_loop() explicitly (not asyncio.run())
"""

import asyncio
import json
import logging
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Protocol

from nonoha_core.node_registry import NodeRegistry
from nonoha_core import nodes as core_nodes

from .graph_loader import (
    activate_graph,
    load_flow_from_path,
)
from .flow_registry import (
    FlowRegistry,
    is_reserved_flow_name,
)
from .message_protocol import (
    MSG_TYPE_JSON,
    MSG_TYPE_EVENT_INPUT,
    build_event_output,
    build_get_output_error,
    build_get_output_response,
    parse_dispatch_input,
)

# Module-level logger
logger = logging.getLogger(__name__)

# Version constant, kept in lockstep with nonoha-engine
VERSION = "1.0.0"


def _error(message: str) -> str:
    """JSON error response — the only non-``*_result`` response type.

    Response contract: every JSON request is answered with either a
    success payload (``type`` names the result) or this error shape.
    """
    return json.dumps({"type": "error", "message": message})


class TransportStrategy(Protocol):
    """Transport layer protocol for communication.

    Implementations:
    - TcpServerStrategy (host-agnostic, in tcp_server_strategy.py)
    - DartBridgeStrategy (host-specific; ships in the Flutter binding's
      ``nonoha-dartbridge`` package, not in this core)
    """

    async def run(self, on_message: Callable[[bytes], bytes | None]) -> None:
        """Run the transport communication loop.

        Args:
            on_message: Callback to handle incoming messages.
                        Returns response bytes or None.
        """
        ...

    def send_event(self, data: bytes) -> None:
        """Send an event to the client (for output events)."""
        ...


@dataclass
class RuntimeConfig:
    """Runtime configuration.

    ``vnf_path`` (optional) names a ``<flow>.vnf`` folder; every graph listed
    in its ``flow.json`` loads into a flow named after the folder stem,
    marked builtin (immutable at runtime). Flows are the only unit of graph
    loading — there is no flow-less graph source.

    ``node_registrars`` injects extension node sets: each entry is a
    ``register_nodes(registry)``-style callable (the convention every nonoha
    node package follows), called after the core nodes are registered. The
    app entry point owns the imports — e.g.
    ``node_registrars=[sci_nodes.register_nodes]``.
    """

    log_path: str | None = None
    vnf_path: str | os.PathLike | None = None
    node_registrars: list[Callable[[NodeRegistry], None]] = field(
        default_factory=list
    )


class Runtime:
    """Main runtime class for nonoha-embedded.

    Manages node registry, graph registry, and message handling.
    Independent of dart_bridge.
    """

    def __init__(self, config: RuntimeConfig | None = None):
        """Initialize the runtime.

        Args:
            config: Runtime configuration. If None, uses defaults.

        Initialization order:
        1. Logging setup (first, so all subsequent logs are captured)
        2. Node registry with all built-in nodes
        3. Graph registry
        """
        self._config = config or RuntimeConfig()
        self._event_loop: asyncio.AbstractEventLoop | None = None
        self._transport: TransportStrategy | None = None

        # Logging state (instance variables)
        self._log_file_path: str | None = None
        self._file_handler: logging.FileHandler | None = None
        self._log_formatter = logging.Formatter(
            "[%(asctime)s.%(msecs)03d] [%(name)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

        # 1. Initialize logging (first)
        self._init_logging(self._config.log_path)
        logger.info(f"Runtime initializing (version={VERSION})")

        # 2. Initialize node registry with built-in nodes
        node_registry = NodeRegistry()
        self._register_builtin_nodes(node_registry)
        self._node_registry = node_registry

        # 3. Initialize flow registry
        self._flow_registry = FlowRegistry()

        logger.info("Runtime initialized")

    @property
    def node_registry(self) -> NodeRegistry:
        """Get the node registry."""
        return self._node_registry

    @property
    def flow_registry(self) -> FlowRegistry:
        """Get the flow registry."""
        return self._flow_registry

    @property
    def version(self) -> str:
        """Get the runtime version."""
        return VERSION

    @property
    def event_loop(self) -> asyncio.AbstractEventLoop | None:
        """Get the current event loop (available during run())."""
        return self._event_loop

    # ========== Logging Methods ==========

    def _init_logging(self, log_path: str | None) -> None:
        """Initialize logging with optional file handler.

        Args:
            log_path: Path to log file, or None for stdout only.
        """
        # Configure root logger
        root_logger = logging.getLogger()
        root_logger.setLevel(logging.DEBUG)

        # Always add stdout handler
        stdout_handler = logging.StreamHandler(sys.stdout)
        stdout_handler.setLevel(logging.DEBUG)
        stdout_handler.setFormatter(self._log_formatter)
        root_logger.addHandler(stdout_handler)

        # Add file handler if path provided
        if log_path:
            try:
                # Ensure parent directory exists
                log_dir = os.path.dirname(log_path)
                if log_dir and not os.path.exists(log_dir):
                    os.makedirs(log_dir, exist_ok=True)

                self._file_handler = logging.FileHandler(
                    log_path, mode="a", encoding="utf-8", delay=False
                )
                self._file_handler.setLevel(logging.DEBUG)
                self._file_handler.setFormatter(self._log_formatter)
                root_logger.addHandler(self._file_handler)
                self._log_file_path = log_path
            except (OSError, IOError) as e:
                logger.warning(f"Cannot write to log file {log_path}: {e}")
                self._log_file_path = None
                self._file_handler = None

    # ========== Node Registration ==========

    def _register_builtin_nodes(self, node_registry: NodeRegistry) -> None:
        """Register all built-in nodes.

        Uses nonoha-core's canonical ``register_nodes`` (the same entry point the
        desktop engine uses) so the embedded node set automatically tracks
        nonoha-core instead of drifting from a hand-maintained list. All core
        nodes are numpy-only (scipy-free) and mobile-safe.

        Extension node sets (e.g. sci-nodes) are injected by the app entry
        point via ``RuntimeConfig.node_registrars`` — this runtime does not
        know specific extension package names.
        """
        # All nonoha-core nodes (numpy-only, scipy-free; includes Window/FFT).
        core_nodes.register_nodes(node_registry)

        for register in self._config.node_registrars:
            register(node_registry)
            logger.info(f"Registered extension nodes via {register!r}")

    # ========== Graph Registration ==========

    def _register_graphs(self) -> None:
        """Load the config flow, if any.

        Called after the event loop is started. Loads the configured .vnf
        into a flow named after its folder stem, marked builtin (immutable
        at runtime). A failure is logged; the runtime still starts.
        """
        if self._config.vnf_path is not None:
            flow_name = Path(self._config.vnf_path).stem
            try:
                self._load_flow(flow_name, self._config.vnf_path, builtin=True)
            except Exception as e:
                logger.error(
                    f"Failed to register flow {self._config.vnf_path}: {e}"
                )

    def _insert_graph(self, flow_name: str, graph) -> None:
        """Insert a built graph into a flow, then activate it.

        Registration must precede activation: ports do not replay values to
        late subscribers, so the registry's output subscriptions have to be in
        place before activation-time dispatches (e.g. value-node defaults).
        On insertion failure the graph is finalized so subscriptions it
        already made (e.g. a bound GraphProxy) are cleaned up.
        """
        try:
            self._flow_registry.add_graph(flow_name, graph)
        except Exception:
            graph.finalize()
            graph.set_parent_flow(None)
            raise
        activate_graph(graph)
        logger.info(f"Registered graph: {flow_name}/{graph.name}")

    def _load_flow(self, flow_name: str, vnf_path, *, builtin: bool = False):
        """Load a .vnf into a new flow, graph by graph, in flow.json order.

        Each graph is inserted (and activated) before the next one is built,
        so GraphProxy nodes can resolve earlier siblings. On failure the
        whole flow is rolled back.
        """
        flow = self._flow_registry.register_flow(flow_name, builtin=builtin)
        try:
            for graph in load_flow_from_path(
                vnf_path, self._node_registry, activate=False, flow=flow
            ):
                self._insert_graph(flow_name, graph)
        except Exception:
            self._flow_registry.unregister_flow(flow_name)
            raise
        return flow

    # ========== Message Handling ==========

    def handle_message(self, data: bytes) -> bytes | None:
        """Handle incoming binary message.

        Args:
            data: Raw message bytes

        Returns:
            Response bytes or None
        """
        if len(data) == 0:
            return None

        msg_type = data[0]

        if msg_type == MSG_TYPE_JSON:
            # JSON message (skip first byte)
            message = data[1:].decode("utf-8")
            response = self._handle_json_message(message)
            if isinstance(response, str):
                return bytes([MSG_TYPE_JSON]) + response.encode("utf-8")
            return response

        elif msg_type == MSG_TYPE_EVENT_INPUT:
            # Binary input event
            self._handle_event_input(data)
            return None  # Fire-and-forget

        else:
            logger.warning(f"Unknown message type: {msg_type}")
            return None

    def _handle_event_input(self, data: bytes) -> None:
        """Handle EVENT_INPUT binary message."""
        try:
            flow_name, graph_name, port_name, values = parse_dispatch_input(data)
            self._flow_registry.dispatch_input(
                flow_name, graph_name, port_name, values
            )
        except Exception as e:
            logger.error(f"Error handling EVENT_INPUT: {e}")

    def _handle_json_message(self, message: str) -> str | bytes:
        """Handle JSON message and return response."""
        try:
            data = json.loads(message)
            msg_type = data.get("type", "unknown")

            if msg_type == "ping":
                return json.dumps({"type": "pong"})

            elif msg_type == "version":
                return json.dumps({"type": "version", "version": VERSION})

            elif msg_type == "test_nonoha_core":
                # Import testing module only when needed
                from .testing import test_nonoha_core

                result = test_nonoha_core(self._node_registry)
                return json.dumps({"type": "test_result", **result})

            elif msg_type == "test_scipy":
                from .testing import test_scipy

                result = test_scipy()
                return json.dumps({"type": "test_result", **result})

            # Flow API. Graphs are addressed by (flow_name, graph_name);
            # graph names are unique within a flow only.
            elif msg_type == "list_flows":
                return json.dumps(
                    {
                        "type": "list_flows_result",
                        "flows": self._flow_registry.list_flows(),
                    }
                )

            elif msg_type == "load_flow":
                vnf_path = data.get("path", "")
                flow_name = Path(vnf_path).stem
                if is_reserved_flow_name(flow_name):
                    return _error(
                        f"Flow name '{flow_name}' is reserved "
                        "(names starting with '_' are runtime-owned)"
                    )
                if self._flow_registry.get_flow(flow_name) is not None:
                    return _error(
                        f"Flow '{flow_name}' already registered "
                        "(unload it first to update)"
                    )
                try:
                    flow = self._load_flow(flow_name, vnf_path)
                except Exception as e:
                    return _error(str(e))
                return json.dumps(
                    {
                        "type": "load_flow_result",
                        "flow": flow_name,
                        "graphs": list(flow.graph_names),
                    }
                )

            elif msg_type == "unload_flow":
                flow_name = data.get("flow_name", "")
                if self._flow_registry.is_builtin(flow_name):
                    return _error(
                        f"Flow '{flow_name}' is builtin and cannot be "
                        "unloaded (builtins are updated with app releases)"
                    )
                if self._flow_registry.unregister_flow(flow_name):
                    logger.info(f"Unregistered flow: {flow_name}")
                    return json.dumps(
                        {
                            "type": "unload_flow_result",
                            "flow": flow_name,
                        }
                    )
                return _error(f"Flow '{flow_name}' not found")

            elif msg_type == "list_graphs":
                flow_name = data.get("flow_name", "")
                if self._flow_registry.get_flow(flow_name) is None:
                    return _error(f"Flow '{flow_name}' not found")
                return json.dumps(
                    {
                        "type": "list_graphs_result",
                        "graphs": self._flow_registry.list_graphs(flow_name),
                    }
                )

            elif msg_type == "list_graph_inputs":
                flow_name = data.get("flow_name", "")
                graph_name = data.get("graph_name", "")
                inputs = self._flow_registry.list_inputs(flow_name, graph_name)
                return json.dumps(
                    {
                        "type": "list_graph_inputs_result",
                        "inputs": inputs,
                    }
                )

            elif msg_type == "list_graph_outputs":
                flow_name = data.get("flow_name", "")
                graph_name = data.get("graph_name", "")
                outputs = self._flow_registry.list_outputs(flow_name, graph_name)
                return json.dumps(
                    {
                        "type": "list_graph_outputs_result",
                        "outputs": outputs,
                    }
                )

            elif msg_type == "get_output":
                flow_name = data.get("flow_name", "")
                graph_name = data.get("graph_name", "")
                port_name = data.get("port_name", "")
                # Always answers binary (0x03): status 0 with value_count 0
                # when nothing is cached yet, status 1 on unknown graph/port.
                # A single response shape keeps the client's wait logic
                # trivial (this pull API is an embedded-only extension; the
                # engine has no equivalent to mirror).
                try:
                    values, v_types = self._flow_registry.get_output(
                        flow_name, graph_name, port_name
                    )
                    if values:
                        return build_get_output_response(values, v_types)
                    return build_get_output_response([], [])
                except ValueError:
                    return build_get_output_error()
                except Exception as e:
                    # The always-binary contract must hold even for
                    # unexpected failures — a JSON error here would leave
                    # the client waiting on a binary frame.
                    logger.error(f"get_output failed unexpectedly: {e}")
                    return build_get_output_error()

            elif msg_type == "update_graph_properties":
                flow_name = data.get("flow_name", "")
                graph_name = data.get("graph_name", "")
                properties = data.get("properties", {})
                if not isinstance(properties, dict):
                    return _error("'properties' must be an object")
                graph = self._flow_registry.get_graph(flow_name, graph_name)
                if graph is None:
                    return _error(
                        f"Graph '{graph_name}' not found in flow '{flow_name}'"
                    )
                # Unknown keys follow the graph-property spec: skipped
                # per-key, only declared keys change.
                graph.set_properties(properties)
                # Configuration contract: the set_properties caller owns
                # the commit (see BaseNodeGraph.flush_outputs).
                graph.flush_outputs()
                return json.dumps({"type": "update_graph_properties_result"})

            else:
                return _error(f"Unknown message type: {msg_type}")

        except json.JSONDecodeError as e:
            return _error(f"Invalid JSON: {e}")
        except Exception as e:
            return _error(f"Error processing message: {e}")

    # ========== Run ==========

    def run(self, transport: TransportStrategy) -> None:
        """Run the runtime with the specified transport strategy.

        Creates an explicit event loop (not using asyncio.run()).

        Args:
            transport: Transport strategy to use for communication.
        """
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._event_loop = loop
        self._transport = transport

        try:
            loop.run_until_complete(self._run_async(transport))
        finally:
            loop.close()
            self._event_loop = None
            self._transport = None

    async def _run_async(self, transport: TransportStrategy) -> None:
        """Async main loop.

        Args:
            transport: Transport strategy to use.
        """
        # 1. Register graphs (after event loop is available)
        self._register_graphs()

        # 2. Set up output callback for push events
        def on_output_event(flow_name, graph_name, port_name, values, vtypes):
            event_bytes = build_event_output(
                flow_name, graph_name, port_name, values, vtypes
            )
            transport.send_event(event_bytes)

        self._flow_registry.set_output_callback(on_output_event)

        # 3. Run transport communication loop
        logger.info("Starting transport...")
        await transport.run(self.handle_message)
