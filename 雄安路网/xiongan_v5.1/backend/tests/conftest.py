"""全局测试夹具：把 backend 根目录加入 sys.path，保证 `from app.*` 可导入。"""

import os
import sys

BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_ROOT not in sys.path:
    sys.path.insert(0, BACKEND_ROOT)

import pytest  # noqa: E402

from app.config import Settings  # noqa: E402
from mock_engine import MockEngine  # noqa: E402


@pytest.fixture
def settings(tmp_path, monkeypatch):
    monkeypatch.delenv("SUMO_HOME", raising=False)
    monkeypatch.chdir(tmp_path)  # 避免读到真实 .env
    return Settings()


def _make_mock_factory():
    def factory():
        e = MockEngine()
        e.connect("mock_net.xml")
        # 链条路网：E1→E2→E3（n1→n2→n3→n4）
        e.add_edge("E1", length=100.0, speed_limit=10.0, road_type="arterial",
                   from_node="n1", to_node="n2")
        e.add_edge("E2", length=100.0, speed_limit=10.0, road_type="secondary",
                   from_node="n2", to_node="n3")
        e.add_edge("E3", length=100.0, speed_limit=8.0, road_type="local",
                   from_node="n3", to_node="n4")
        e.add_tls("T1", num_phases=3, phase=0, duration=10.0, lanes=["E1_0", "E2_0"])
        return e
    return factory


@pytest.fixture
def mock_factory():
    return _make_mock_factory()


@pytest.fixture
def mock_engine():
    return _make_mock_factory()()

