"""Tests for nonoha_embedded.graph_loader (.vnf / .vng loading)."""

import json
from pathlib import Path

import pytest
from nonoha_core import nodes as core_nodes
from nonoha_core.node_registry import NodeRegistry

from nonoha_embedded.graph_loader import (
    activate_graph,
    load_flow_from_path,
    load_graph_from_dict,
    load_graph_from_path,
)

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture()
def registry() -> NodeRegistry:
    reg = NodeRegistry()
    core_nodes.register_nodes(reg)
    return reg


def _invalid_edge_count(graph) -> int:
    return sum(1 for edge in graph._edges.values() if not edge.valid)


class TestLoadFlowFromPath:
    def test_loads_all_graphs(self, registry):
        graphs = load_flow_from_path(FIXTURES / "basic.vnf", registry)
        assert [g.name for g in graphs] == ["basic"]

    def test_graph_structure(self, registry):
        (graph,) = load_flow_from_path(FIXTURES / "basic.vnf", registry)
        assert set(graph.nodes) == {
            "Clock_1",
            "WaveGenerator_2",
            "Greater_3",
            "RealNumberValue_4",
        }
        assert set(graph.inputs) == {"enabled"}
        assert set(graph.outputs) == {"wave", "over"}
        assert len(graph.get_edges()) == 6
        assert _invalid_edge_count(graph) == 0

    def test_dispatch_flags_applied(self, registry):
        (graph,) = load_flow_from_path(FIXTURES / "basic.vnf", registry)
        greater = graph.nodes["Greater_3"]
        assert greater.inputs["in"].dispatch_flags == [True, False]

    def test_properties_applied(self, registry):
        (graph,) = load_flow_from_path(FIXTURES / "basic.vnf", registry)
        clock = graph.nodes["Clock_1"]
        assert clock.properties["fs"] == 100.0
        assert clock.properties["pack_size"] == 10

    def test_missing_flow_json(self, registry, tmp_path):
        vnf = tmp_path / "empty.vnf"
        vnf.mkdir()
        with pytest.raises(FileNotFoundError, match="Flow file not found"):
            load_flow_from_path(vnf, registry)

    def test_wrong_suffix(self, registry, tmp_path):
        with pytest.raises(ValueError, match=".vnf extension"):
            load_flow_from_path(tmp_path, registry)

    def test_missing_graph_in_flow(self, registry, tmp_path):
        vnf = tmp_path / "flow.vnf"
        vnf.mkdir()
        (vnf / "flow.json").write_text('{"graphs": ["ghost"]}')
        with pytest.raises(RuntimeError, match="Failed to load graph 'ghost'"):
            load_flow_from_path(vnf, registry)


class TestLoadGraphFromPath:
    def test_name_from_folder_stem(self, registry):
        graph = load_graph_from_path(FIXTURES / "data.vng", registry)
        assert graph.name == "data"

    def test_persist_data_dir(self, registry):
        vng = FIXTURES / "data.vng"
        graph = load_graph_from_path(vng, registry)
        assert graph.persist is not None
        assert graph.persist.data_dir_path == vng / "data"
        assert (graph.persist.data_dir_path / "marker.txt").is_file()

    def test_broken_edge_recorded_invalid(self, registry):
        graph = load_graph_from_path(FIXTURES / "broken_edge.vng", registry)
        assert len(graph.get_edges()) == 2
        assert _invalid_edge_count(graph) == 1

    def test_wrong_suffix(self, registry, tmp_path):
        with pytest.raises(ValueError, match=".vng extension"):
            load_graph_from_path(tmp_path, registry)

    def test_missing_graph_json(self, registry, tmp_path):
        vng = tmp_path / "empty.vng"
        vng.mkdir()
        with pytest.raises(FileNotFoundError, match="Graph file not found"):
            load_graph_from_path(vng, registry)

    def test_vjb_only_unsupported(self, registry, tmp_path):
        vng = tmp_path / "enc.vng"
        vng.mkdir()
        (vng / "graph.vjb").write_bytes(b"\x00")
        with pytest.raises(ValueError, match="graph.vjb is not supported"):
            load_graph_from_path(vng, registry)

    def test_corrupt_json(self, registry, tmp_path):
        vng = tmp_path / "bad.vng"
        vng.mkdir()
        (vng / "graph.json").write_text("{not json")
        with pytest.raises(ValueError, match="Failed to load graph file"):
            load_graph_from_path(vng, registry)


class TestLoadGraphFromDict:
    def test_unknown_node_type(self, registry):
        data = {"nodes": {"Mystery_1": {"type": "NoSuchType"}}}
        with pytest.raises(
            ValueError, match="'Mystery_1' \\(type: NoSuchType\\) in graph 'g'"
        ):
            load_graph_from_dict("g", data, registry)

    def test_activation_emits_value_defaults(self, registry):
        data = {
            "nodes": {
                "RealNumberValue_1": {
                    "type": "RealNumberValue",
                    "properties": {"value_type": "f32", "default": 1.5},
                }
            },
            "outputs": {"out": {"types": ["f32"]}},
            "edges": [{"source": "RealNumberValue_1.value", "target": ".out"}],
        }
        graph = load_graph_from_dict("g", data, registry, activate=False)

        received = []
        graph.outputs["out"].subscribe(1, received.append)

        activate_graph(graph)
        assert received, "activation should dispatch the value node's default"
        assert float(received[0][0][0]) == 1.5
