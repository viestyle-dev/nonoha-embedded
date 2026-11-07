"""Graph loader for nonoha-engine graph files.

Loads graphs authored by nonoha-engine, either from an in-memory dict or
directly from the on-disk workspace format:

- ``<flow>.vnf/`` — a flow folder: ``flow.json`` (``{"graphs": [names]}``) plus
  one ``graphs/<name>.vng/`` folder per graph.
- ``<name>.vng/`` — a graph folder: ``graph.json`` plus an optional ``data/``
  directory. Nodes reference files in ``data/`` by bare filename and resolve
  them at runtime via ``parent_graph.persist.data_dir_path``.

Only the current nonoha-engine file format is supported. Encrypted graphs
(``graph.vjb``) are not supported.
"""

import json
import logging
from os import PathLike
from pathlib import Path
from typing import Any, Iterator

from nonoha_core.node import (
    BaseNode,
    BaseNodeFlow,
    BaseNodeGraph,
    Edge,
    NodeGraphPersist,
)
from nonoha_core.node_registry import NodeRegistry
from nonoha_core.port import (
    StaticTypeInputPort,
    StaticTypeOutputPort,
)
from nonoha_core.data_type import PropertyDataType, name_to_port_value_type

logger = logging.getLogger(__name__)


class ReadOnlyGraphDirPersist(NodeGraphPersist):
    """Persist backed by a ``.vng`` folder, used only to expose ``data_dir_path``.

    Nodes resolve data files via ``parent_graph.persist.data_dir_path / name``
    (= ``<graph>.vng/data/<name>``). The embedded runtime never writes graph
    definitions back, so all persistence mutations are no-ops. The data
    directory is not created eagerly; writer nodes create it on demand.
    """

    def __init__(self, graph_dir_path: str | PathLike):
        self._graph_dir_path = Path(graph_dir_path)

    @property
    def data_dir_path(self) -> Path:
        return self._graph_dir_path / "data"

    def add_input_port(self, port: StaticTypeInputPort) -> None:
        pass

    def remove_input_port(self, port_name: str) -> None:
        pass

    def add_output_port(self, port: StaticTypeOutputPort) -> None:
        pass

    def remove_output_port(self, port_name: str) -> None:
        pass

    def add_node(self, node: BaseNode) -> None:
        pass

    def remove_node(self, node_name: str) -> None:
        pass

    def update_edges(self, edges: dict[tuple[str, str], Edge]) -> None:
        pass

    def update_node(self, node: BaseNode) -> None:
        pass

    def update_properties(self, node: BaseNode) -> None:
        pass

    def update_graph_properties(
        self, properties: dict[str, PropertyDataType]
    ) -> None:
        pass


def activate_graph(graph: BaseNodeGraph) -> None:
    """Activate all nodes and fire graph-loaded callbacks.

    Mirrors the engine's post-load step: nodes set up runtime resources in
    ``activate()`` (value nodes emit their defaults there), then graph-loaded
    subscribers are notified. Per the configuration contract
    (``BaseNodeGraph.flush_outputs``), the loader commits the whole
    activation batch with a single flush at the end.
    """
    for node in graph.nodes.values():
        node._do_activate()
    graph.notify_graph_loaded()
    graph.flush_outputs()


def load_graph_from_dict(
    graph_name: str,
    graph_data: dict[str, Any],
    registry: NodeRegistry,
    *,
    persist: NodeGraphPersist | None = None,
    activate: bool = True,
    flow: BaseNodeFlow | None = None,
) -> BaseNodeGraph:
    """Build a graph from a parsed ``graph.json`` dictionary.

    Args:
        graph_name: Name for the graph.
        graph_data: Parsed graph JSON: ``{properties, nodes, inputs, outputs,
            edges}``. Optional keys may be absent (the engine saves with
            ``exclude_defaults``).
        registry: Node registry used to create nodes.
        persist: Optional persist to attach (provides ``data_dir_path`` to
            nodes). ``load_graph_from_path`` supplies one automatically.
        activate: Activate nodes after building. Pass ``False`` when the
            caller needs to subscribe to outputs before activation-time
            dispatches (then call :func:`activate_graph` itself).
        flow: Flow the graph will belong to. Sets ``parent_flow`` before node
            properties are applied so graph-referencing nodes (GraphProxy)
            can resolve previously loaded siblings during the build. The
            loader does NOT insert the graph into the flow — insertion goes
            through the FlowRegistry, which also wires output subscriptions.

    Returns:
        The constructed :class:`BaseNodeGraph`.

    On a build failure the partially built graph is finalized before the
    error propagates, so nodes that already grabbed resources or
    subscriptions (e.g. a GraphProxy bound to a sibling graph) are cleaned
    up.
    """
    graph = BaseNodeGraph(graph_name, graph_data.get("properties", {}))
    if flow is not None:
        graph.set_parent_flow(flow)
    if persist is not None:
        graph.set_persist(persist)

    try:
        _build_graph(graph, graph_name, graph_data, registry)
    except Exception:
        graph.finalize()
        graph.set_parent_flow(None)
        raise

    if activate:
        activate_graph(graph)

    return graph


def _build_graph(
    graph: BaseNodeGraph,
    graph_name: str,
    graph_data: dict[str, Any],
    registry: NodeRegistry,
) -> None:
    # 1. Create and configure nodes
    for node_name, node_data in graph_data.get("nodes", {}).items():
        node_type = node_data["type"]
        try:
            node = registry.create_node(node_type, node_name)
        except Exception as e:
            raise ValueError(
                f"Failed to create node '{node_name}' (type: {node_type}) "
                f"in graph '{graph_name}'"
            ) from e
        graph.add_node(node)

        if properties := node_data.get("properties"):
            node.set_properties(properties)

        if property_groups := node_data.get("property_groups"):
            node.set_property_groups(property_groups)

        # Apply dispatch_flags, iterating the node's actual ports so entries
        # for ports the node no longer has are skipped with a warning.
        node_inputs = node_data.get("inputs", {})
        for port_name in node_inputs:
            if port_name not in node.inputs:
                logger.warning(
                    f"Graph '{graph_name}' node '{node_name}': "
                    f"unknown input port '{port_name}' ignored"
                )
        for port_name, port in node.inputs.items():
            port_model = node_inputs.get(port_name)
            if port_model is None:
                continue
            flags = port_model.get("dispatch_flags")
            if flags is None:
                continue
            if len(port.dispatch_flags) != len(flags):
                logger.warning(
                    f"Graph '{graph_name}' node '{node_name}' port "
                    f"'{port_name}': dispatch_flags length mismatch ignored"
                )
                continue
            port.dispatch_flags = [bool(f) for f in flags]

    # 2. Create graph input ports
    for input_name, port_data in graph_data.get("inputs", {}).items():
        value_types = [
            name_to_port_value_type(t) for t in port_data.get("types", [])
        ]
        graph.add_input_port(StaticTypeInputPort(input_name, value_types))

    # 3. Create graph output ports
    for output_name, port_data in graph_data.get("outputs", {}).items():
        value_types = [
            name_to_port_value_type(t) for t in port_data.get("types", [])
        ]
        graph.add_output_port(StaticTypeOutputPort(output_name, value_types))

    # 4. Connect edges. Endpoints are "Node.port" (with optional ".N"
    #    sub-index) or ".port" for graph-level I/O. Failed edges are recorded
    #    as invalid instead of aborting the whole graph.
    for edge in graph_data.get("edges", []):
        source = edge["source"]
        target = edge["target"]
        try:
            src_node, src_port = source.split(".", 1)
            tgt_node, tgt_port = target.split(".", 1)

            if src_node == "" and tgt_node != "":
                graph.connect_input_to_node(src_port, tgt_node, tgt_port)
            elif src_node != "" and tgt_node == "":
                graph.connect_node_to_output(src_node, src_port, tgt_port)
            elif src_node != "" and tgt_node != "":
                graph.connect_nodes(src_node, src_port, tgt_node, tgt_port)
            else:
                raise ValueError(
                    "graph-input to graph-output edges are not supported"
                )
        except (ValueError, KeyError) as e:
            logger.warning(f"Edge {source} -> {target} stored as invalid: {e}")
            graph.add_invalid_edge(source, target)


def load_graph_from_path(
    vng_path: str | PathLike,
    registry: NodeRegistry,
    *,
    activate: bool = True,
    flow: BaseNodeFlow | None = None,
) -> BaseNodeGraph:
    """Load a graph from a ``<name>.vng`` folder.

    The graph name is the folder stem; ``graph.json`` holds the definition and
    the optional ``data/`` directory is exposed to nodes via the attached
    persist.
    """
    vng_path = Path(vng_path)
    if vng_path.suffix != ".vng":
        raise ValueError(f"Graph directory must have .vng extension: {vng_path}")

    graph_file = vng_path / "graph.json"
    if not graph_file.is_file():
        if (vng_path / "graph.vjb").is_file():
            raise ValueError(
                f"Encrypted graph.vjb is not supported by nonoha-embedded; "
                f"export '{vng_path.name}' with graph.json"
            )
        raise FileNotFoundError(f"Graph file not found in: {vng_path}")

    try:
        with open(graph_file, encoding="utf-8") as f:
            graph_data = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        raise ValueError(f"Failed to load graph file: {graph_file}") from e

    return load_graph_from_dict(
        vng_path.stem,
        graph_data,
        registry,
        persist=ReadOnlyGraphDirPersist(vng_path),
        activate=activate,
        flow=flow,
    )


def load_flow_from_path(
    vnf_path: str | PathLike,
    registry: NodeRegistry,
    *,
    activate: bool = True,
    flow: BaseNodeFlow | None = None,
) -> Iterator[BaseNodeGraph]:
    """Load the graphs of a ``<flow>.vnf`` folder, one at a time.

    Reads ``flow.json`` and builds ``graphs/<name>.vng`` for each listed
    graph name, in order. Validation (suffix, ``flow.json``, presence of
    every listed ``.vng``) happens eagerly at call time; only the graph
    builds are deferred to iteration.

    Returns an iterator that yields each graph as it is built. Consumers
    that pass ``flow=`` must insert each yielded graph into the flow before
    requesting the next one — graph-referencing nodes (GraphProxy) resolve
    earlier siblings through the flow while the next graph is being built.
    """
    vnf_path = Path(vnf_path)
    if vnf_path.suffix != ".vnf":
        raise ValueError(f"Flow directory must have .vnf extension: {vnf_path}")

    flow_file = vnf_path / "flow.json"
    if not flow_file.is_file():
        raise FileNotFoundError(f"Flow file not found: {flow_file}")

    try:
        with open(flow_file, encoding="utf-8") as f:
            flow_data = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        raise ValueError(f"Failed to load flow file: {flow_file}") from e

    graph_names = flow_data.get("graphs", [])
    for graph_name in graph_names:
        vng_dir = vnf_path / "graphs" / f"{graph_name}.vng"
        if not (vng_dir / "graph.json").is_file():
            raise RuntimeError(
                f"Failed to load graph '{graph_name}' in flow: {vnf_path}"
            )

    def _iter_graphs() -> Iterator[BaseNodeGraph]:
        for graph_name in graph_names:
            vng_dir = vnf_path / "graphs" / f"{graph_name}.vng"
            try:
                yield load_graph_from_path(
                    vng_dir, registry, activate=activate, flow=flow
                )
            except Exception as e:
                raise RuntimeError(
                    f"Failed to load graph '{graph_name}' in flow: {vnf_path}"
                ) from e

    return _iter_graphs()
