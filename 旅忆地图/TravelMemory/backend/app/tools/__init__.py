"""工具层（`tools/`）—— 阶段 3 W3-1 的落地位置。

依赖方向（PROJECT.md §二、[4]）：`agent → nodes → tools → services → models`。
本包只能向下依赖 `services / models / schemas / core`，**严禁 import `app.agent` 与 `app.api`** ——
`tests/test_layering.py` 会把这条红线钉在 CI 上。

为什么这个目录之前是空的却不是缺陷：设计文档 §5 把它列为阶段 3 的预留位（PRD §6.4）。
本文件（连同 `tests/test_layering.py`）就是"开始填它"的第一块砖：
先把包建出来、把依赖方向守卫装上，再往里加 `base.py` / `registry.py`（T3-02）。
"""

from __future__ import annotations
