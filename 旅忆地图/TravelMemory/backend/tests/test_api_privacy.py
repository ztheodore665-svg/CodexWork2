"""`POST /api/v1/privacy/obfuscate` 的接口层测试。

与 `tests/test_api_agent.py` 同一套做法：直接 `TestClient(app)`，不连数据库、
不 skip。区别只在于本文件要**真的发请求**（这个接口是同步纯函数，不涉及 IO）。

关于开关：本项目的 feature flag 默认全关（PROJECT.md §二、[8]），
所以"默认 404"和"flag 开后才能用"这两条都是**必须**覆盖的用例 ——
少测前者，将来有人把开关默认值改成 True 没人会发现。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import app
from app.privacy.obfuscate import MODE_RADIUS_M
from app.schemas.privacy import COORD_SYSTEM_WGS84

client = TestClient(app)

#: 杭州西湖（WGS84）
LAT, LNG = 30.2422, 120.1494

_URL = "/api/v1/privacy/obfuscate"


@pytest.fixture
def privacy_disabled(monkeypatch: pytest.MonkeyPatch):
    """开关显式关掉（同时屏蔽本机 `.env` 的影响）。

    为什么不用"不设环境变量"来表达"关闭"：本机 `.env` 里就写着
    `PRIVACY_ENABLED=false`，但换一台机器或有人手贱改了它就不再成立 ——
    显式写 false，断言才是在考验"默认关闭"这件事本身。
    """
    monkeypatch.setenv("PRIVACY_ENABLED", "false")
    get_settings.cache_clear()
    try:
        yield
    finally:
        get_settings.cache_clear()


@pytest.fixture
def privacy_enabled(monkeypatch: pytest.MonkeyPatch):
    """开启 `privacy` 开关，用完清掉 `get_settings` 的 lru_cache。"""
    monkeypatch.setenv("PRIVACY_ENABLED", "true")
    get_settings.cache_clear()
    try:
        yield
    finally:
        get_settings.cache_clear()


def test_route_is_registered() -> None:
    """路由必须真的挂上：它不会因为任何单测失败而自动显形，靠这条盯着。"""
    spec = app.openapi()
    assert _URL in spec["paths"], f"路由没挂上：{list(spec['paths'])}"
    assert "post" in spec["paths"][_URL]


def test_disabled_flag_returns_404(privacy_disabled) -> None:
    """未启用 → **404**（能力不存在），不是 403（存在但没权限）。"""
    resp = client.post(_URL, json={"lat": LAT, "lng": LNG})
    assert resp.status_code == 404


@pytest.mark.parametrize("mode", ["approx", "city"])
def test_enabled_returns_full_contract(privacy_enabled, mode: str) -> None:
    """开启后字段齐全：`coord_system` 恒为 WGS84，`radius` 等于该模式的承诺半径。"""
    resp = client.post(_URL, json={"lat": LAT, "lng": LNG, "mode": mode, "epsilon": 1.0})
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {"lat", "lng", "radius", "mode", "epsilon", "coord_system"}
    assert body["mode"] == mode
    assert body["epsilon"] == 1.0
    assert body["radius"] == MODE_RADIUS_M[mode]
    assert body["coord_system"] == COORD_SYSTEM_WGS84 == "WGS84"
    # 加噪必须真的改变了坐标，否则接口等于没做事
    assert (body["lat"], body["lng"]) != (LAT, LNG)
    assert -90.0 <= body["lat"] <= 90.0
    assert -180.0 <= body["lng"] < 180.0


def test_default_mode_is_approx(privacy_enabled) -> None:
    """不传 mode / epsilon 时走 `approx` + `epsilon=1.0`（契约里的默认值）。"""
    body = client.post(_URL, json={"lat": LAT, "lng": LNG}).json()
    assert body["mode"] == "approx"
    assert body["epsilon"] == 1.0
    assert body["radius"] == MODE_RADIUS_M["approx"]


def test_exact_mode_returns_input_unchanged(privacy_enabled) -> None:
    """`exact` 必须原样返回：这是用户显式勾选"分享精确位置"时走的路。"""
    body = client.post(_URL, json={"lat": LAT, "lng": LNG, "mode": "exact"}).json()
    assert body["lat"] == LAT
    assert body["lng"] == LNG
    assert body["radius"] == 0.0


def test_repeated_calls_give_different_points(privacy_enabled) -> None:
    """同样的入参两次调用不该得到同一个点 —— 可重放就能被外部校准出真实坐标。"""
    first = client.post(_URL, json={"lat": LAT, "lng": LNG}).json()
    second = client.post(_URL, json={"lat": LAT, "lng": LNG}).json()
    assert (first["lat"], first["lng"]) != (second["lat"], second["lng"])


@pytest.mark.parametrize("lat", [91.0, -90.5])
def test_invalid_latitude_returns_422(privacy_enabled, lat: float) -> None:
    """越界坐标由 pydantic 拦在 422 —— handler 里没有、也不该有手写校验。"""
    assert client.post(_URL, json={"lat": lat, "lng": LNG}).status_code == 422


@pytest.mark.parametrize("lng", [181.0, -180.5])
def test_invalid_longitude_returns_422(privacy_enabled, lng: float) -> None:
    assert client.post(_URL, json={"lat": LAT, "lng": lng}).status_code == 422


@pytest.mark.parametrize("epsilon", [0.0, -1.0, 11.0])
def test_invalid_epsilon_returns_422(privacy_enabled, epsilon: float) -> None:
    """0 会被除零、负数反向保护、>10 超出预算上限 —— 全部 422。"""
    body = {"lat": LAT, "lng": LNG, "epsilon": epsilon}
    assert client.post(_URL, json=body).status_code == 422


def test_invalid_mode_returns_422(privacy_enabled) -> None:
    body = {"lat": LAT, "lng": LNG, "mode": "xxx"}
    assert client.post(_URL, json=body).status_code == 422


def test_missing_coordinates_returns_422(privacy_enabled) -> None:
    assert client.post(_URL, json={"lat": LAT}).status_code == 422


def test_error_shape_has_the_fastapi_detail_field(privacy_enabled) -> None:
    """错误响应体用 FastAPI 标准的 `detail`，前端按一个地方解析即可。"""
    body = client.post(_URL, json={"lat": 91.0, "lng": LNG}).json()
    assert "detail" in body
