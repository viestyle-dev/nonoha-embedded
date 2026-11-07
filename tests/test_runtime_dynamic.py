"""Tests for the dynamic flow lifecycle API (load_flow / unload_flow).

Flows are the only unit of graph loading: a .vnf loads as a flow named
after its folder stem, and unloading is flow-granular.
"""

import json
from pathlib import Path

from nonoha_embedded.message_protocol import (
    MSG_TYPE_GET_OUTPUT_RESPONSE,
    MSG_TYPE_JSON,
)
from nonoha_embedded.runtime import Runtime, RuntimeConfig

FIXTURES = Path(__file__).parent / "fixtures"
BASIC_VNF = FIXTURES / "basic.vnf"


def _send(runtime: Runtime, payload: dict) -> bytes:
    data = bytes([MSG_TYPE_JSON]) + json.dumps(payload).encode("utf-8")
    response = runtime.handle_message(data)
    assert response is not None
    return response


def _send_json(runtime: Runtime, payload: dict) -> dict:
    response = _send(runtime, payload)
    assert response[0] == MSG_TYPE_JSON
    return json.loads(response[1:].decode("utf-8"))


class TestLoadFlowMessage:
    def test_load_registers_flow(self):
        rt = Runtime(RuntimeConfig())
        res = _send_json(rt, {"type": "load_flow", "path": str(BASIC_VNF)})
        assert res["type"] == "load_flow_result"
        assert res["flow"] == "basic"
        assert res["graphs"] == ["basic"]
        assert "basic" in rt.flow_registry.list_graphs("basic")

    def test_duplicate_flow_errors(self):
        # Flows are never overwritten; updates are unload + load, host-driven.
        rt = Runtime(RuntimeConfig())
        _send_json(rt, {"type": "load_flow", "path": str(BASIC_VNF)})
        original = rt.flow_registry.get_graph("basic", "basic")
        res = _send_json(rt, {"type": "load_flow", "path": str(BASIC_VNF)})
        assert res["type"] == "error"
        assert rt.flow_registry.get_graph("basic", "basic") is original
        assert all(node.is_active for node in original._nodes.values())

    def test_missing_path_errors(self):
        rt = Runtime(RuntimeConfig())
        res = _send_json(
            rt, {"type": "load_flow", "path": str(FIXTURES / "nope.vnf")}
        )
        assert res["type"] == "error"


class TestUnloadFlowMessage:
    def test_unload_finalizes_and_unsubscribes(self):
        rt = Runtime(RuntimeConfig())
        _send_json(rt, {"type": "load_flow", "path": str(BASIC_VNF)})
        graph = rt.flow_registry.get_graph("basic", "basic")
        reg_key = id(rt.flow_registry)
        assert all(
            reg_key in port._callback_keys for port in graph.outputs.values()
        )

        res = _send_json(rt, {"type": "unload_flow", "flow_name": "basic"})
        assert res["type"] == "unload_flow_result"
        assert rt.flow_registry.get_flow("basic") is None
        assert all(
            reg_key not in port._callback_keys for port in graph.outputs.values()
        )
        assert all(not node.is_active for node in graph._nodes.values())
        assert graph.parent_flow is None

    def test_unload_then_load_updates(self):
        # The host-orchestrated update path: unload -> load.
        rt = Runtime(RuntimeConfig())
        _send_json(rt, {"type": "load_flow", "path": str(BASIC_VNF)})
        first = rt.flow_registry.get_graph("basic", "basic")
        _send_json(rt, {"type": "unload_flow", "flow_name": "basic"})
        res = _send_json(rt, {"type": "load_flow", "path": str(BASIC_VNF)})
        assert res["type"] == "load_flow_result"
        assert rt.flow_registry.get_graph("basic", "basic") is not first

    def test_unload_missing_errors(self):
        rt = Runtime(RuntimeConfig())
        res = _send_json(rt, {"type": "unload_flow", "flow_name": "ghost"})
        assert res["type"] == "error"


class TestBuiltinImmutability:
    def test_builtin_flow_unload_refused(self):
        rt = Runtime(RuntimeConfig(vnf_path=BASIC_VNF))
        rt._register_graphs()
        # The config .vnf loads into a builtin flow named after its stem.
        assert rt.flow_registry.is_builtin("basic")

        res = _send_json(rt, {"type": "unload_flow", "flow_name": "basic"})
        assert res["type"] == "error"
        assert "builtin" in res["message"]
        assert rt.flow_registry.get_flow("basic") is not None


class TestGetOutputBinary:
    def test_empty_returns_binary_count_zero(self):
        # No JSON fallback: an uncached port answers 0x03/status=0/count=0,
        # which the client decodes as an empty list (previously this path
        # hung the client forever).
        rt = Runtime(RuntimeConfig())
        _send_json(rt, {"type": "load_flow", "path": str(BASIC_VNF)})
        res = _send(
            rt,
            {
                "type": "get_output",
                "flow_name": "basic",
                "graph_name": "basic",
                "port_name": "wave",
            },
        )
        assert res[0] == MSG_TYPE_GET_OUTPUT_RESPONSE
        assert res[1] == 0x00  # status: success
        assert res[2] == 0x00  # value_count: 0

    def test_unknown_graph_returns_binary_error(self):
        rt = Runtime(RuntimeConfig())
        res = _send(
            rt,
            {
                "type": "get_output",
                "flow_name": "basic",
                "graph_name": "ghost",
                "port_name": "x",
            },
        )
        assert res[0] == MSG_TYPE_GET_OUTPUT_RESPONSE
        assert res[1] == 0x01  # status: error

    def test_unexpected_error_still_answers_binary(self, monkeypatch):
        # The always-binary contract must hold for ANY failure: a JSON
        # error would leave the client waiting on a binary frame forever.
        rt = Runtime(RuntimeConfig())

        def boom(*_args, **_kwargs):
            raise RuntimeError("boom")

        monkeypatch.setattr(rt.flow_registry, "get_output", boom)
        res = _send(
            rt,
            {
                "type": "get_output",
                "flow_name": "f",
                "graph_name": "g",
                "port_name": "p",
            },
        )
        assert res[0] == MSG_TYPE_GET_OUTPUT_RESPONSE
        assert res[1] == 0x01  # status: error
