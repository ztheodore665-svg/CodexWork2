from app.schemes.base import SchemeContext
from app.schemes.scheme3.graph import RoadNetwork


def _net(engine):
    return RoadNetwork(SchemeContext(engine=engine))


def test_network_restricted_edge(mock_engine):
    n = _net(mock_engine)
    n.add_restricted_edge("E2", "construction")
    assert n.is_restricted("E2")
    assert n.verify_route(["E1", "E2", "E3"]) is False   # 含受限边
    assert n.verify_route(["E1", "E3"]) is False         # 不连续
    assert n.verify_route(["E1", "E2"]) is False         # 受限
    n.remove_restricted_edge("E2")
    assert n.verify_route(["E1", "E2", "E3"]) is True    # 移除后合法


def test_network_free_flow_time(mock_engine):
    n = _net(mock_engine)
    assert n.free_flow_time("E1") == 10.0   # 100m / 10 m/s


def test_network_successor_chain(mock_engine):
    n = _net(mock_engine)
    assert mock_engine.get_edge_successors("E1") == ["E2"]
    assert mock_engine.get_edge_successors("E2") == ["E3"]
    assert n.edge_count() == 3
