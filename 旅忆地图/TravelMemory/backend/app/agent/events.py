"""两种运行时（纯 Python 管道 / LangGraph）**共用**的 SSE 事件构造器。

为什么单独拆一个模块：阶段 4 的出口条件之一是
「两种运行时评测结果可对比」—— 如果两套编排各自拼一套事件，
前端就要写两套解析、评测也要跑两条口径，迟早走样。
事件格式属于**对外契约**，必须与编排方式解耦。
"""

from __future__ import annotations

from typing import Any

from app.agent.state import PipelineState


def parsed_event(state: PipelineState) -> dict:
    parsed = state.parsed
    payload: dict[str, Any] = {"degraded": True if parsed is None else parsed.degraded}
    if parsed is not None:
        payload.update(
            {
                "keywords": parsed.keywords,
                "places": parsed.places,
                "themes": parsed.themes,
                "time_range": parsed.time_range,
                "time_source": parsed.time_source,
                "time_label": parsed.time_label,
            }
        )
    return {"type": "parsed", "payload": payload}


def tool_start_event(state: PipelineState) -> dict:
    """工具即将调用（前端显示"正在查地图…"）。

    **必须在真正调用之前发出**，否则用户会看到一段没有解释的空白等待 ——
    工具调用是唯一会引入"外部网络耗时"的环节，不提示的话首字延迟会显得像卡死。
    """
    decision = getattr(state, "tool_decision", None)
    selected = getattr(decision, "tool", None) if decision else None
    if selected is None:
        return {"type": "tool_start", "payload": {"tool": None, "reason": "无需调用工具"}}
    payload: dict[str, Any] = {
        "type": "tool_start",
        "payload": {
            "tool": selected,
            "reason": getattr(decision, "reason", ""),
            "source": getattr(decision, "source", "rule"),
        },
    }
    return payload


def tool_result_event(state: PipelineState) -> dict:
    """工具调用结束（前端渲染结果卡片）。

    三个字段是前端必须区分的三种状态：
      - `ok + cache_hit`：查到了，且来自缓存（省了一次外部调用）
      - `ok`：查到了
      - `!ok`：工具不可用，本次回答只用记忆（要在界面上说清楚，不能假装查过）
    """
    decision = getattr(state, "tool_decision", None)
    payload: dict[str, Any] = {"tool": None, "ok": False}
    if decision is not None:
        payload = {
            "tool": getattr(decision, "tool", None),
            "ok": bool(getattr(decision, "ok", False)),
            "error": getattr(decision, "error", ""),
            "cache_hit": bool(getattr(decision, "cache_hit", False)),
            "latency_ms": int(getattr(decision, "latency_ms", 0) or 0),
            "evidence_count": int(getattr(decision, "evidence_count", 0) or 0),
            "source": getattr(decision, "source", ""),
        }
    return {"type": "tool_result", "payload": payload}


def evidence_event(state: PipelineState) -> dict:
    """证据 + 本次检索实际生效的过滤条件。

    把过滤条件一起给前端是有意的：一旦出现"我以为搜到了却没搜到"，
    用户/开发能在界面上直接看到系统把问题理解成了哪个时间窗 / 哪些地名。
    """
    debug = getattr(state, "retrieve_debug", None)
    return {
        "type": "evidence",
        "payload": {
            "blocks": [
                {
                    "index": block.index,
                    "ref": str(block.ref),
                    "score": round(block.score, 4),
                    "content": block.content,
                }
                for block in state.blocks
            ],
            "filter": getattr(debug, "filter", {}),
            "filter_fallback": getattr(debug, "filter_fallback", False),
            "vector_hits": getattr(debug, "vector_hits", 0),
            "lexical_hits": getattr(debug, "lexical_hits", 0),
        },
    }


def done_payload(state: PipelineState) -> dict:
    return {
        "refused": state.refused,
        "refuse_reason": state.refuse_reason,
        "citations": [str(ref) for ref in state.citations],
        "latency_ms": state.latency_ms,
        "token_usage": state.token_usage,
    }
