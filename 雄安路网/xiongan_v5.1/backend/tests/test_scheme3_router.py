from app.schemes.base import SchemeContext
from app.schemes.scheme3.graph import RoadNetwork
from app.schemes.scheme3.router import RoutePlanner
from app.schemes.scheme3.weights import EdgeWeightManager
from mock_engine import MockEngine


def _branched_network():
    """E1→E2→E5 与 E1→E3→E4→E5 两条路径（E1 出口分支到 E2/E3）。"""
    e = MockEngine()
    e.connect("branched.net.xml")
    e.add_edge("E1", from_node="n1", to_node="n2")
    e.add_edge("E2", from_node="n2", to_node="n3")
    e.add_edge("E3", from_node="n2", to_node="n4")
    e.add_edge("E4", from_node="n4", to_node="n3")
    e.add_edge("E5", from_node="n3", to_node="n5")
    return e


def _router(engine):
    ctx = SchemeContext(engine=engine)
    net = RoadNetwork(ctx)
    w = EdgeWeightManager(ctx)
    w.init()
    return RoutePlanner(ctx, net, w)


def test_router_shortest_path_avoids_restricted():
    e = _branched_network()
    r = _router(e)
    r.network.add_restricted_edge("E2", "construction")
    path = r.shortest_path("E1", "E5")
    assert path is not None and "E2" not in path
    assert path == ["E1", "E3", "E4", "E5"]


def test_router_shortest_path_returns_none_if_no_route():
    e = _branched_network()
    r = _router(e)
    r.network.add_restricted_edge("E2", "x")
    r.network.add_restricted_edge("E3", "x")
    assert r.shortest_path("E1", "E5") is None


def test_router_k_shortest_distinct():
    e = _branched_network()
    r = _router(e)
    paths = r.k_shortest("E1", "E5", k=3)
    assert len(paths) >= 2
    assert len({tuple(p) for p in paths}) == len(paths)


def test_router_plan_batch_load_balance():
    e = _branched_network()
    r = _router(e)
    for i in range(6):
        e.add_vehicle(f"v{i}", edges=["E1", "E5"])
    reqs = [{"vehicle_id": f"v{i}", "from": "E1", "to": "E5"} for i in range(6)]
    plans = r.plan_batch(reqs)
    assert all(p["ok"] for p in plans)
    routes = {tuple(p["route"]) for p in plans}
    assert len(routes) >= 2   # 超过阈值后分散到两条路线


def test_router_recommend_shape():
    e = _branched_network()
    r = _router(e)
    recs = r.recommend("E1", "E5", k=2)
    assert recs and "route" in recs[0]
    assert "total_weight" in recs[0] and "edge_count" in recs[0]
