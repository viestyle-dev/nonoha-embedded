"""Flow registry and graph I/O operations.

Graphs are organized by flow (a named collection of graphs loaded together,
usually one per ``.vnf``). Mutations are flow-centric: a flow is registered
and unregistered as a unit, and graphs are inserted into a flow one by one —
the insertion order is what lets GraphProxy resolve earlier siblings while a
flow is still loading. Graph names are unique within a flow only; different
flows may reuse the same graph name.
"""

from typing import Callable

from nonoha_core.node import BaseNodeFlow, BaseNodeGraph
from nonoha_core.data_type import PortValueType
from nonoha_core.port import PortValue

# Type alias for output callback:
# (flow_name, graph_name, port_name, values, value_types)
OutputCallback = Callable[
    [str, str, str, list[PortValue], list[PortValueType]], None
]

# Flow names starting with "_" are reserved for future runtime use and are
# rejected by load_flow (reserving them now is non-breaking; carving them
# out later would not be).
def is_reserved_flow_name(name: str) -> bool:
    return name.startswith("_")


class FlowRegistry:
    """Manages loaded flows and provides graph I/O operations.

    Policy checks (builtin immutability, reserved names) belong to the
    caller (Runtime message handlers); this class is mechanical so internal
    paths like load rollback can always clean up.
    """

    def __init__(self):
        self._flows: dict[str, BaseNodeFlow[BaseNodeGraph]] = {}
        self._builtin_flows: set[str] = set()
        # (flow_name, graph_name) -> {port_name: values}
        self._output_cache: dict[tuple[str, str], dict[str, list[PortValue]]] = {}
        self._output_callback: OutputCallback | None = None

    def set_output_callback(self, callback: OutputCallback | None):
        """Set callback for output events.

        The callback will be called whenever a graph output port emits values.
        Args:
            callback: Function(flow_name, graph_name, port_name, values, value_types)
        """
        self._output_callback = callback

    # ----- flow-centric mutations -----

    def register_flow(
        self, flow_name: str, *, builtin: bool = False
    ) -> BaseNodeFlow[BaseNodeGraph]:
        """Create and register an empty flow (instances are registry-owned).

        ``builtin`` marks config-loaded flows, which the runtime treats as
        immutable at runtime.
        """
        if flow_name in self._flows:
            raise ValueError(f"Flow '{flow_name}' already registered")
        flow: BaseNodeFlow[BaseNodeGraph] = BaseNodeFlow()
        self._flows[flow_name] = flow
        if builtin:
            self._builtin_flows.add(flow_name)
        return flow

    def add_graph(self, flow_name: str, graph: BaseNodeGraph):
        """Insert a graph into a flow and subscribe to its outputs.

        Raises if the flow is unknown or the graph name already exists in
        the flow. Registration must precede activation: ports do not replay
        values to late subscribers, so the output subscriptions have to be
        in place before activation-time dispatches.
        """
        flow = self._flows.get(flow_name)
        if flow is None:
            raise ValueError(f"Flow '{flow_name}' not found")
        flow.add_graph(graph)  # sets parent_flow; raises on duplicate name

        self._output_cache[(flow_name, graph.name)] = {}
        for port_name, port in graph.outputs.items():

            def make_handler(
                f_name: str, g_name: str, p_name: str, p_vtypes: list[PortValueType]
            ):
                def handler(values: list[PortValue]):
                    self._output_cache[(f_name, g_name)][p_name] = values
                    if self._output_callback:
                        self._output_callback(f_name, g_name, p_name, values, p_vtypes)

                return handler

            port.subscribe(
                id(self),
                make_handler(flow_name, graph.name, port_name, port.value_type_list),
            )

    def remove_graph(self, flow_name: str, graph_name: str) -> bool:
        """Remove one graph from a flow, releasing its runtime resources.

        Unsubscribes this registry from the outputs, then ``flow.remove_graph``
        finalizes the graph (cancelling node-owned resources) and clears its
        parent_flow.

        Returns:
            True if the graph existed and was removed.
        """
        flow = self._flows.get(flow_name)
        if flow is None:
            return False
        graph = flow.get_graph(graph_name)
        if graph is None:
            return False
        for port in graph.outputs.values():
            port.unsubscribe(id(self))
        flow.remove_graph(graph_name)
        self._output_cache.pop((flow_name, graph_name), None)
        return True

    def unregister_flow(self, flow_name: str) -> bool:
        """Unregister a flow, finalizing all its graphs.

        Returns:
            True if the flow existed and was removed.
        """
        flow = self._flows.get(flow_name)
        if flow is None:
            return False
        for graph_name in list(flow.graph_names):
            self.remove_graph(flow_name, graph_name)
        del self._flows[flow_name]
        self._builtin_flows.discard(flow_name)
        return True

    # ----- lookups -----

    def get_flow(self, flow_name: str) -> BaseNodeFlow[BaseNodeGraph] | None:
        return self._flows.get(flow_name)

    def is_builtin(self, flow_name: str) -> bool:
        return flow_name in self._builtin_flows

    def list_flows(self) -> list[dict]:
        """Return the flow topology: name, builtin flag and graph names."""
        return [
            {
                "name": name,
                "builtin": name in self._builtin_flows,
                "graphs": list(flow.graph_names),
            }
            for name, flow in self._flows.items()
        ]

    def list_graphs(self, flow_name: str) -> list[str]:
        """Return graph names of one flow (empty for unknown flows)."""
        flow = self._flows.get(flow_name)
        if flow is None:
            return []
        return list(flow.graph_names)

    def get_graph(self, flow_name: str, graph_name: str) -> BaseNodeGraph | None:
        flow = self._flows.get(flow_name)
        if flow is None:
            return None
        return flow.get_graph(graph_name)

    # ----- graph I/O (wire facade) -----

    def list_inputs(self, flow_name: str, graph_name: str) -> list[dict]:
        """Return list of input port info for a graph."""
        graph = self.get_graph(flow_name, graph_name)
        if not graph:
            return []
        result = []
        for name, port in graph.inputs.items():
            result.append(
                {
                    "name": name,
                    "values": [
                        {"type": vt.data_type.name, "ndim": vt.ndim}
                        for vt in port.value_type_list
                    ],
                }
            )
        return result

    def list_outputs(self, flow_name: str, graph_name: str) -> list[dict]:
        """Return list of output port info for a graph."""
        graph = self.get_graph(flow_name, graph_name)
        if not graph:
            return []
        result = []
        for name, port in graph.outputs.items():
            result.append(
                {
                    "name": name,
                    "values": [
                        {"type": vt.data_type.name, "ndim": vt.ndim}
                        for vt in port.value_type_list
                    ],
                }
            )
        return result

    def dispatch_input(
        self, flow_name: str, graph_name: str, port_name: str, values: list[PortValue]
    ):
        """Dispatch values to a graph input port."""
        graph = self.get_graph(flow_name, graph_name)
        if not graph:
            raise ValueError(f"Graph '{flow_name}/{graph_name}' not found")
        port = graph.inputs.get(port_name)
        if not port:
            raise ValueError(f"Input port '{port_name}' not found")
        port.dispatch(values)

    def get_output(self, flow_name: str, graph_name: str, port_name: str):
        """Get cached output values from a graph output port.

        Returns:
            Tuple of (values, value_type_list) or (None, value_type_list)
            if no values cached
        """
        graph = self.get_graph(flow_name, graph_name)
        if not graph:
            raise ValueError(f"Graph '{flow_name}/{graph_name}' not found")
        port = graph.outputs.get(port_name)
        if not port:
            raise ValueError(f"Output port '{port_name}' not found")
        values = self._output_cache.get((flow_name, graph_name), {}).get(port_name)
        return values, port.value_type_list
