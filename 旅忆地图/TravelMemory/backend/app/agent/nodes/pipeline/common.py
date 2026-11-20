"""管道节点共用的小工具。

这些都是纯函数，放在这里是为了避免每个节点都重写一遍处理 LLM 输出的脏活。
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any

#: LLM 经常给答案加引号、代码块 fence 或"改写后的句子："这类前缀
_FENCE_RE = re.compile(r"^```(?:json|markdown|text)?\s*|\s*```$", re.MULTILINE)


def today_text() -> str:
    """给 LLM 注入「今天是哪天」。

    时间解析依赖这一步：模型自己不知道当前日期，"去年秋天""前年五一"都无从换算。
    """
    now = datetime.now()
    weekday = "一二三四五六日"[now.weekday()]
    return f"{now.strftime('%Y-%m-%d')}（周{weekday}）"


def format_history(history: list[dict[str, str]]) -> str:
    """把最近若干轮对话拼成可读文本供 LLM 消解指代。

    阶段 0 只是简单的滑动窗口；阶段 4 的短期记忆会替换这里。
    """
    if not history:
        return "（本轮之前没有对话）"
    lines = ["最近对话："]
    for turn in history[-6:]:
        role = "用户" if turn.get("role") == "user" else "助手"
        lines.append(f"{role}：{turn.get('content', '').strip()}")
    return "\n".join(lines)


#: LLM 有时会在答案前加个标签，如"改写后的提问：xxx"
_LABEL_RE = re.compile(r"^(?:改写后?[的]?(?:问题|提问|查询|句子)|改写结果|原始问题)\s*[:：]\s*")


def strip_prefix_label(text: str) -> str:
    """去掉模型自己加的答案标签前缀。"""
    return _LABEL_RE.sub("", text).strip()


def clean_llm_text(text: str, *, max_chars: int = 300) -> str:
    """清理 LLM 输出：去代码块围栏、去包裹引号、过长则判为异常返回空串。"""
    cleaned = _FENCE_RE.sub("", text).strip().strip("\"'“”‘’").strip()
    if len(cleaned) > max_chars:
        # 说明模型没按指令只输出一句话，宁可退回原问题也不要污染检索串
        return ""
    return cleaned


def extract_json_object(text: str) -> dict[str, Any] | None:
    """从可能带围栏/前后废话的输出里提取第一个 JSON 对象。"""
    cleaned = _FENCE_RE.sub("", text).strip()
    try:
        data = json.loads(cleaned)
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None
