"""LLM 智能体 API：对话（带工具调用）与状态查询。

说明：端点用普通 def（FastAPI 自动放入线程池执行），
避免 LLM 推理 / TraCI 查询阻塞事件循环。
"""

from fastapi import APIRouter, Body, Request

from app.api.common import ok
from app.agent.agent import agent_status, run_agent
from app.agent.llm import MODEL_OPTIONS, resolve_config
from app.agent.tools import TOOL_SCHEMAS

router = APIRouter(prefix="/agent", tags=["agent"])


@router.post("/chat")
def agent_chat(request: Request, body: dict = Body(...)):
    """发送自然语言指令，Agent 可调用平台工具（读指标/规划路径/注入事件/调参/生成报告）。"""
    runtime = request.app.state.runtime
    if runtime.session is None:
        return ok({"reply": "请先启动仿真：POST /api/v1/simulate/start（建议 scheme=scheme_2）",
                   "tool_calls": []})
    message = str(body.get("message", ""))
    if not message.strip():
        return ok({"reply": "请输入指令", "tool_calls": []})
    reply, log = run_agent(runtime, message)
    return ok({"reply": reply, "tool_calls": log})


@router.get("/status")
def agent_status_endpoint(request: Request):
    return ok(agent_status(request.app.state.runtime._llm_model))


@router.post("/model")
def agent_switch_model(request: Request, body: dict = Body(...)):
    """运行时切换 LLM 模型（仅限当前 provider 的可选免费模型）。"""
    rt = request.app.state.runtime
    model = str(body.get("model", "")).strip()
    provider = resolve_config().get("provider", "zhipu")
    allowed = MODEL_OPTIONS.get(provider, [])
    if model not in allowed:
        return ok({"ok": False, "message": f"模型 {model} 不可用，可选：{allowed}"})
    rt._llm_model = model
    return ok({"ok": True, "model": model, "available_models": allowed})


@router.post("/reset")
def agent_reset(request: Request):
    """清空 Agent 会话记忆与对比基线。"""
    rt = request.app.state.runtime
    n = len(rt._agent_conv)
    rt._agent_conv = []
    rt._agent_baseline = None
    return ok({"ok": True, "cleared_messages": n})


@router.get("/tools")
def agent_tools():
    """Agent 技能（工具）清单：名称、用途、参数说明，供前端"工具详情"展示。"""
    out = []
    for t in TOOL_SCHEMAS:
        fn = t["function"]
        params = fn.get("parameters", {})
        props = params.get("properties", {})
        required = set(params.get("required", []))
        arg_docs = []
        for k, v in props.items():
            rng = None
            if "minimum" in v or "maximum" in v:
                rng = f"{v.get('minimum', '')}~{v.get('maximum', '')}"
            arg_docs.append({
                "name": k,
                "desc": v.get("description", ""),
                "required": k in required,
                "enum": v.get("enum") or None,
                "range": rng,
            })
        out.append({
            "name": fn["name"],
            "description": fn.get("description", ""),
            "args": arg_docs,
        })
    return ok(out)
