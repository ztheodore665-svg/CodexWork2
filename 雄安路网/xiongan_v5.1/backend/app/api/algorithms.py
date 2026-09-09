"""标准化算法接口 API（MCP-like client 侧）。

GET    /algorithms                 工具清单
GET    /algorithms/{id}            schema（参数/观测/指标/能力声明 + 当前值）
GET    /algorithms/{id}/state      运行状态（参数 + 观测快照 + 内部指标）
POST   /algorithms/{id}/config     设置参数（校验后应用）
POST   /algorithms/{id}/action     通用动作调用
"""

from fastapi import APIRouter, Body, Request

from app.api.common import ok
from app.algorithms.adapter import get_adapter, list_algorithm_ids

router = APIRouter(prefix="/algorithms", tags=["algorithms"])


@router.get("")
def list_algorithms(request: Request):
    """算法清单（含未激活方案，active 字段区分）。"""
    rt = request.app.state.runtime
    out = []
    for aid in list_algorithm_ids(rt):
        try:
            adapter = get_adapter(rt, aid)
        except KeyError:
            continue
        s = adapter.schema()
        out.append({
            "id": aid,
            "kind": s.get("kind"),
            "description": s.get("description", ""),
            "active": s.get("active", False),
            "params": [p["key"] for p in s.get("params", [])],
        })
    return ok(out)


@router.get("/{algorithm_id}")
def algorithm_schema(request: Request, algorithm_id: str):
    """单个算法的完整声明（含当前参数值）。"""
    try:
        adapter = get_adapter(request.app.state.runtime, algorithm_id)
    except KeyError:
        return ok({"id": algorithm_id, "active": False,
                   "message": "算法不存在"})
    return ok(adapter.schema())


@router.get("/{algorithm_id}/state")
def algorithm_state(request: Request, algorithm_id: str):
    """运行状态：当前参数 + 统一观测快照 + 内部指标。"""
    try:
        adapter = get_adapter(request.app.state.runtime, algorithm_id)
    except KeyError:
        return ok({"id": algorithm_id, "active": False, "message": "算法不存在"})
    return ok(adapter.state())


@router.post("/{algorithm_id}/config")
def algorithm_config(request: Request, algorithm_id: str,
                     body: dict = Body(...)):
    """设置算法参数（按声明校验范围/枚举后应用）。"""
    try:
        adapter = get_adapter(request.app.state.runtime, algorithm_id)
    except KeyError:
        return ok({"ok": False, "message": "算法不存在"})
    return ok(adapter.config(body.get("params") or {}))


@router.post("/{algorithm_id}/action")
def algorithm_action(request: Request, algorithm_id: str,
                     body: dict = Body(...)):
    """通用动作调用（如 switch_mode / switch_tod_plan / reroute）。"""
    try:
        adapter = get_adapter(request.app.state.runtime, algorithm_id)
    except KeyError:
        return ok({"ok": False, "message": "算法不存在"})
    return ok(adapter.action(str(body.get("action", "")),
                             body.get("params") or {}))
