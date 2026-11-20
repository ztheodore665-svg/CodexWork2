"""Prompt 加载器（版本化）。

**改 prompt 必须同时改 `PROMPT_VERSION`**（PROJECT.md §五）：
否则 `EvalResult` 里记的版本号与实际 prompt 不对应，`make eval-compare` 的归因结论是错的。

模板变量统一用 `{{name}}` 占位（不用 str.format，因为 prompt 里常有 JSON 花括号会被误伤）。
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

#: 每次修改 app/agent/prompts/ 下的任何文件都要递增此版本号
#: （阶段 5 W5-1：新增 eval_faithfulness.md —— 只影响 judge 调用，
#:   但按约定仍整体递增，否则 judge 结果无法与 prompt 版本对应上）
PROMPT_VERSION = "pv2026.10-stage5.3"

PROMPT_DIR = Path(__file__).parent
_PLACEHOLDER = re.compile(r"\{\{\s*(\w+)\s*\}\}")


@lru_cache(maxsize=32)
def load(name: str) -> str:
    """读取 prompts 目录下的 markdown 模板（带缓存，进程内只读一次）。"""
    path = PROMPT_DIR / f"{name}.md"
    if not path.exists():
        raise FileNotFoundError(f"prompt 模板不存在: {path}")
    return path.read_text(encoding="utf-8")


def render(name: str, **variables: object) -> str:
    """渲染模板：`{{var}}` 替换为对应值。

    未提供的占位符会被原样保留 —— 目的就是让你在 trace 里一眼看到漏传了什么，
    而不是静默变成空字符串。
    """
    template = load(name)

    def _replace(match: re.Match[str]) -> str:
        key = match.group(1)
        if key in variables:
            return str(variables[key])
        return match.group(0)

    return _PLACEHOLDER.sub(_replace, template).strip()


def reload() -> None:
    """开发时热重载（改了 prompt 文件不想重启进程）。"""
    load.cache_clear()
