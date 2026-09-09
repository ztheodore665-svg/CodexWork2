from fastapi.testclient import TestClient

from app.config import Settings
from app.core.session import SessionError
from app.main import create_app
from app.ws.manager import WSManager

SUMMARY = {
    "edge_count": 80, "intersection_count": 26, "tls_count": 20,
    "total_lane_length_km": 5.5, "network_density_km_per_km2": 15.0,
}


class MockRuntime:
    """镜像 AppRuntime 公共接口，返回固定数据，供 API 层测试。"""

    def __init__(self, ws, settings):
        self.ws = ws
        self.settings = settings

    def start(self, params):
        if params.get("net_path") == "missing":
            raise SessionError(1006, "仿真连接失败: 文件不存在")
        return {"session_id": "abc123", "status": {"state": "running"}}

    def stop(self):
        return {"ok": True}

    def pause(self):
        return {"ok": True}

    def resume(self):
        return {"ok": True}

    def step_n(self, n):
        return {"ok": True}

    def set_speed(self, speed):
        return {"ok": True}

    def status(self):
        return {"state": "running", "session_id": "abc123", "step": 5,
                "sim_time": 5, "scheme": "none", "vehicle_count": 12}

    def network(self):
        return {"type": "FeatureCollection", "features": []}

    def network_summary(self):
        return dict(SUMMARY)

    def realtime_metrics(self):
        return {"overall": {"vehicle_count": 12, "avg_speed": 5.0,
                            "avg_delay": 3.0, "total_throughput": 30,
                            "avg_queue_length": 2.0, "avg_waiting_time": 1.0},
                "intersections": {"T1": {"queue_length": 2}}}

    def history_metrics(self, metric, start, end, interval, scope="overall"):
        return [{"step": 0, "value": 1.0}, {"step": 60, "value": 2.0}]

    def list_schemes(self):
        return [{"id": "scheme_1", "name": "scheme_1",
                 "description": "", "available": True}]

    def scheme_config(self, scheme_id, action, params):
        return {"ok": True, "action": action}

    def scheme_status(self, scheme_id):
        return {"ok": True, "mode": "auto"}

    def inject_event(self, event_type, params):
        return {"ok": True, "event_type": event_type}

    def list_events(self):
        return [{"event_type": "construction", "message": "施工"}]

    def health(self):
        return {"service": "ok", "version": "1.0.0",
                "simulation": self.status(), "ws_clients": 1}


def _make_client():
    app = create_app(settings=Settings(), runtime_factory=lambda ws, s: MockRuntime(ws, s))
    return TestClient(app)


def test_health():
    with _make_client() as client:
        r = client.get("/api/v1/health")
        assert r.status_code == 200
        body = r.json()
        assert body["code"] == 0 and body["data"]["service"] == "ok"


def test_simulate_start_without_config_returns_1006():
    with _make_client() as client:
        r = client.post("/api/v1/simulate/start", json={"net_path": "missing"})
        body = r.json()
        assert body["code"] == 1006
        assert body["data"] is None


def test_network_summary_shape():
    with _make_client() as client:
        r = client.get("/api/v1/network/summary")
        d = r.json()["data"]
        for k in ["edge_count", "intersection_count", "tls_count",
                  "total_lane_length_km", "network_density_km_per_km2"]:
            assert k in d


def test_metrics_realtime():
    with _make_client() as client:
        r = client.get("/api/v1/metrics/realtime")
        assert "overall" in r.json()["data"]


def test_schemes_flow():
    with _make_client() as client:
        r = client.get("/api/v1/schemes")
        assert r.json()["data"][0]["id"] == "scheme_1"
        r = client.post("/api/v1/schemes/scheme_1/config",
                        json={"action": "recalculate", "params": {}})
        assert r.json()["data"]["ok"] is True


def test_events_inject():
    with _make_client() as client:
        r = client.post("/api/v1/events/inject",
                        json={"event_type": "construction",
                              "params": {"edge_ids": ["E1"]}})
        assert r.json()["data"]["ok"] is True


def test_unknown_route_returns_404():
    with _make_client() as client:
        r = client.get("/api/v1/does-not-exist")
        assert r.status_code == 404


def test_ws_client_message_error_does_not_crash():
    with _make_client() as client:
        with client.websocket_connect("/ws") as ws:
            first = ws.receive_json()  # connected 消息
            assert first["type"] == "connected"
            ws.send_text("not-json")
            msg = ws.receive_json()
            assert msg["type"] == "simulation_event"
