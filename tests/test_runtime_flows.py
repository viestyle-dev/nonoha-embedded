"""Tests for the flow-scoped runtime: load_flow/unload_flow/list_flows,
GraphProxy resolution within a flow, per-flow graph name scoping, binary
frame flow fields, and node registrar injection."""

import json
from pathlib import Path

import numpy as np

from nonoha_core.data_type import DataType, PortValueType
from nonoha_core.port import port_values_to_bytes

from nonoha_embedded.message_protocol import (
    MSG_TYPE_EVENT_INPUT,
    MSG_TYPE_JSON,
)
from nonoha_embedded.runtime import Runtime, RuntimeConfig

FIXTURES = Path(__file__).parent / "fixtures"
PROXY_VNF = FIXTURES / "proxy.vnf"
PROXY_B_VNF = FIXTURES / "proxy_b.vnf"
ORPHAN_VNF = FIXTURES / "orphan.vnf"
PROPS_VNF = FIXTURES / "props.vnf"


def _send_json(runtime: Runtime, payload: dict) -> dict:
    data = bytes([MSG_TYPE_JSON]) + json.dumps(payload).encode("utf-8")
    response = runtime.handle_message(data)
    assert response is not None
    assert response[0] == MSG_TYPE_JSON
    return json.loads(response[1:].decode("utf-8"))


def _dispatch(rt: Runtime, flow: str, graph: str, port: str, value: float):
    rt.flow_registry.dispatch_input(
        flow, graph, port, [np.array([value], dtype=np.float32)]
    )


def _output(rt: Runtime, flow: str, graph: str, port: str):
    values, _ = rt.flow_registry.get_output(flow, graph, port)
    return values


class TestLoadFlow:
    def test_load_flow_message(self):
        rt = Runtime(RuntimeConfig())
        res = _send_json(rt, {"type": "load_flow", "path": str(PROXY_VNF)})
        assert res["type"] == "load_flow_result"
        assert res["flow"] == "proxy"
        assert res["graphs"] == ["source", "main"]

        flows = _send_json(rt, {"type": "list_flows"})["flows"]
        assert {
            "name": "proxy",
            "builtin": False,
            "graphs": ["source", "main"],
        } in flows

    def test_duplicate_flow_errors(self):
        rt = Runtime(RuntimeConfig())
        _send_json(rt, {"type": "load_flow", "path": str(PROXY_VNF)})
        res = _send_json(rt, {"type": "load_flow", "path": str(PROXY_VNF)})
        assert res["type"] == "error"

    def test_reserved_flow_name_rejected(self, tmp_path):
        rt = Runtime(RuntimeConfig())
        res = _send_json(
            rt, {"type": "load_flow", "path": str(tmp_path / "_standalone.vnf")}
        )
        assert res["type"] == "error"
        assert "reserved" in res["message"]

    def test_unload_flow_finalizes_all_graphs(self):
        rt = Runtime(RuntimeConfig())
        _send_json(rt, {"type": "load_flow", "path": str(PROXY_VNF)})
        source = rt.flow_registry.get_graph("proxy", "source")
        main = rt.flow_registry.get_graph("proxy", "main")

        res = _send_json(rt, {"type": "unload_flow", "flow_name": "proxy"})
        assert res["type"] == "unload_flow_result"
        assert rt.flow_registry.get_flow("proxy") is None
        for graph in (source, main):
            assert all(not node.is_active for node in graph._nodes.values())
            assert graph.parent_flow is None


class TestGraphProxyInFlow:
    def test_proxy_forwards_within_builtin_flow(self):
        # Startup path: RuntimeConfig(vnf_path=...) loads a builtin flow.
        rt = Runtime(RuntimeConfig(vnf_path=PROXY_VNF))
        rt._register_graphs()
        assert rt.flow_registry.is_builtin("proxy")

        _dispatch(rt, "proxy", "main", "x", -1.5)
        values = _output(rt, "proxy", "main", "y")
        assert values is not None
        np.testing.assert_array_equal(
            values[0], np.array([1.5], dtype=np.float32)
        )

    def test_same_graph_names_coexist_and_stay_independent(self):
        rt = Runtime(RuntimeConfig())
        _send_json(rt, {"type": "load_flow", "path": str(PROXY_VNF)})
        _send_json(rt, {"type": "load_flow", "path": str(PROXY_B_VNF)})

        _dispatch(rt, "proxy", "main", "x", -2.0)
        _dispatch(rt, "proxy_b", "main", "x", -5.0)

        np.testing.assert_array_equal(
            _output(rt, "proxy", "main", "y")[0],
            np.array([2.0], dtype=np.float32),
        )
        np.testing.assert_array_equal(
            _output(rt, "proxy_b", "main", "y")[0],
            np.array([5.0], dtype=np.float32),
        )

    def test_proxy_does_not_resolve_across_flows(self):
        # orphan.vnf contains only "main", whose proxy references "source".
        # "source" exists in the separately loaded "proxy" flow, but the
        # resolution scope is the proxy's own flow -> stays unbound (loads
        # with a warning).
        rt = Runtime(RuntimeConfig())
        _send_json(rt, {"type": "load_flow", "path": str(PROXY_VNF)})
        res = _send_json(rt, {"type": "load_flow", "path": str(ORPHAN_VNF)})
        assert res["type"] == "load_flow_result"
        proxy = rt.flow_registry.get_graph("orphan", "main").get_node(
            "GraphProxy_1"
        )
        assert proxy.inputs == {}
        assert proxy.outputs == {}


class TestBinaryFramesWithFlowField:
    def test_event_input_frame_dispatches(self):
        rt = Runtime(RuntimeConfig(vnf_path=PROXY_VNF))
        rt._register_graphs()

        buf = bytearray()
        buf.append(MSG_TYPE_EVENT_INPUT)
        for token in (b"proxy", b"main"):
            buf.append(len(token))
            buf.extend(token)
        buf.append(0x00)  # event_type: INPUT
        buf.append(len(b"x"))
        buf.extend(b"x")
        port_values_to_bytes(
            [np.array([-4.0], dtype=np.float32)],
            [PortValueType(DataType.Float32)],
            buf,
        )

        assert rt.handle_message(bytes(buf)) is None  # fire-and-forget
        np.testing.assert_array_equal(
            _output(rt, "proxy", "main", "y")[0],
            np.array([4.0], dtype=np.float32),
        )

    def test_output_callback_receives_flow_name(self):
        rt = Runtime(RuntimeConfig(vnf_path=PROXY_VNF))
        rt._register_graphs()
        events: list[tuple[str, str, str]] = []
        rt.flow_registry.set_output_callback(
            lambda flow, graph, port, values, vtypes: events.append(
                (flow, graph, port)
            )
        )

        _dispatch(rt, "proxy", "main", "x", 1.0)

        assert ("proxy", "main", "y") in events


class TestUpdateGraphProperties:
    # props.vnf/main declares {"gain_enabled": true} and wires a
    # GraphPropertiesChanged (selector: gain_enabled, i8) to the "enabled"
    # output.

    def test_update_stores_property_and_emits(self):
        rt = Runtime(RuntimeConfig())
        _send_json(rt, {"type": "load_flow", "path": str(PROPS_VNF)})

        res = _send_json(
            rt,
            {
                "type": "update_graph_properties",
                "flow_name": "props",
                "graph_name": "main",
                "properties": {"gain_enabled": False},
            },
        )

        assert res["type"] == "update_graph_properties_result"
        graph = rt.flow_registry.get_graph("props", "main")
        assert graph.properties["gain_enabled"] is False
        values = _output(rt, "props", "main", "enabled")
        assert values
        np.testing.assert_array_equal(
            values[0], np.array([0], dtype=np.int8)
        )

    def test_unknown_property_is_skipped_and_leaves_graph_unchanged(self):
        # Unknown keys follow the graph-property spec: skipped per-key, so
        # an all-unknown update succeeds without changing anything.
        rt = Runtime(RuntimeConfig())
        _send_json(rt, {"type": "load_flow", "path": str(PROPS_VNF)})

        res = _send_json(
            rt,
            {
                "type": "update_graph_properties",
                "flow_name": "props",
                "graph_name": "main",
                "properties": {"no_such_key": 1},
            },
        )

        assert res["type"] == "update_graph_properties_result"
        graph = rt.flow_registry.get_graph("props", "main")
        assert graph.properties == {"gain_enabled": True}
        # Output still holds the activation-time snapshot (True -> 1);
        # a skipped-only update must not re-emit anything.
        values = _output(rt, "props", "main", "enabled")
        assert values
        np.testing.assert_array_equal(values[0], np.array([1], dtype=np.int8))

    def test_mixed_update_applies_declared_and_skips_unknown(self):
        rt = Runtime(RuntimeConfig())
        _send_json(rt, {"type": "load_flow", "path": str(PROPS_VNF)})

        res = _send_json(
            rt,
            {
                "type": "update_graph_properties",
                "flow_name": "props",
                "graph_name": "main",
                "properties": {"gain_enabled": False, "no_such_key": 1},
            },
        )

        assert res["type"] == "update_graph_properties_result"
        graph = rt.flow_registry.get_graph("props", "main")
        assert graph.properties == {"gain_enabled": False}
        values = _output(rt, "props", "main", "enabled")
        assert values
        np.testing.assert_array_equal(values[0], np.array([0], dtype=np.int8))

    def test_unknown_graph_errors(self):
        rt = Runtime(RuntimeConfig())
        _send_json(rt, {"type": "load_flow", "path": str(PROPS_VNF)})

        res = _send_json(
            rt,
            {
                "type": "update_graph_properties",
                "flow_name": "props",
                "graph_name": "nope",
                "properties": {"gain_enabled": False},
            },
        )

        assert res["type"] == "error"
        assert "not found" in res["message"]

    def test_non_object_properties_errors(self):
        rt = Runtime(RuntimeConfig())
        _send_json(rt, {"type": "load_flow", "path": str(PROPS_VNF)})

        res = _send_json(
            rt,
            {
                "type": "update_graph_properties",
                "flow_name": "props",
                "graph_name": "main",
                "properties": [1, 2],
            },
        )

        assert res["type"] == "error"


class TestNodeRegistrars:
    def test_registrar_is_invoked(self):
        from nonoha_core.node import BaseNode

        class MarkerNode(BaseNode):
            def __init__(self, name: str):
                super().__init__(name, "Marker")

        def register(registry):
            registry.register_node("Marker", MarkerNode)

        rt = Runtime(RuntimeConfig(node_registrars=[register]))
        node = rt.node_registry.create_node("Marker", "Marker_1")
        assert node.type == "Marker"
