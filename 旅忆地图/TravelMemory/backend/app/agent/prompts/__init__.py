"""Prompt 模板（版本化）。

**改动这里任何一个 .md 文件，都要同步递增 `loader.PROMPT_VERSION`**，
否则评测无法归因（PROJECT.md §五）。
"""

from app.agent.prompts.loader import PROMPT_VERSION, load, reload, render

__all__ = ["PROMPT_VERSION", "load", "reload", "render"]
