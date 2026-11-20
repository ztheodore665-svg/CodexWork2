"""API 层的冒烟测试（不连数据库）。

只断言"路由挂上了、契约没错"这种结构性事实 ——
真的发一次 POST 会立刻去连数据库，那是标 `db` 的用例的事。

为什么值得为这种"看起来很弱"的断言写一个文件：
路由被误删、`prefix` 写错、schema 字段改名，都是改别处代码时的连带伤害，
而它们不会让任何单测变红 —— 只有这里会。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.main import app
from app.schemas.agent import ChatRequest


def test_chat_endpoint_is_registered() -> None:
    spec = app.openapi()
    path = "/api/v1/agent/chat"
    assert path in spec["paths"], f"路由没挂上：{list(spec['paths'])}"
    assert "post" in spec["paths"][path]


def test_health_reports_current_stage() -> None:
    """健康检查刻意不查库，所以它能在任何环境下作为探针用。"""
    payload = TestClient(app).get("/health").json()
    assert payload["status"] == "ok"
    assert payload["stage"] == 16
    assert payload["prompt_version"]


def test_chat_request_requires_query() -> None:
    with pytest.raises(ValidationError):
        ChatRequest()  # type: ignore[call-arg]


def test_chat_request_defaults_are_optional() -> None:
    body = ChatRequest(query="我去年秋天去过哪些古镇？")
    assert body.session_id is None
    assert body.history == []
