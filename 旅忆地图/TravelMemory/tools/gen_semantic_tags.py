#!/usr/bin/env python
"""从 Python 词表生成前端常量（阶段 16：六份名单合一）。

## 为什么需要它

六类语义标签曾在 4 个地方各写一遍（后端 py / 后端 yaml / 主前端 ts / 小应用 ts），
另有 2 份测试靠正则从 py 里抠数组来断言"顺序一致"。测试能发现不一致，
但**不能阻止不一致发生** —— 改词表的人仍然要记得改四个文件。

生成器把这件事变成单向的：`backend/app/utils/semantic_tags.py` 是唯一原件，
其余文件带 `GENERATED` 头、由本脚本产出。CI 跑 `--check`，忘跑生成就红。

## 为什么生成两份 TS 而不是建一个包

主应用与小应用之间不能互相 import（`tools/check-import-boundaries.mjs` 会拦）。
为 6 个字符串建一个 workspace 包，构建、版本、发布都要跟着走；
生成两份文件的成本是 0，代价只是"改完要跑一次脚本"——而那正是 `--check` 要盯的。

## 用法

    python tools/gen_semantic_tags.py          # 写入
    python tools/gen_semantic_tags.py --check  # 只校验，CI 用
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.utils.semantic_tags import (  # noqa: E402
    SEMANTIC_TAG_LABELS,
    SEMANTIC_TAG_VERSION,
    SEMANTIC_TAGS,
)

TARGETS = (
    ROOT / "frontend" / "src" / "generated" / "semanticTags.ts",
    ROOT / "apps" / "trailshare-standalone" / "src" / "generated" / "semanticTags.ts",
)

HEADER = """/**
 * 六类语义标签 —— **自动生成，不要手改**。
 *
 * 唯一真相源：`backend/app/utils/semantic_tags.py`。
 * 重新生成：`python tools/gen_semantic_tags.py`
 * CI 校验：`python tools/gen_semantic_tags.py --check`
 *
 * 顺序即契约（平票裁决依据），生成器按源文件顺序输出，**只能追加不能重排**。
 */

"""


def render() -> str:
    tags = ", ".join(json.dumps(tag, ensure_ascii=False) for tag in SEMANTIC_TAGS)
    labels = ",\n".join(
        f"  {json.dumps(tag, ensure_ascii=False)}: {json.dumps(SEMANTIC_TAG_LABELS[tag], ensure_ascii=False)}" for tag in SEMANTIC_TAGS
    )
    return (
        HEADER
        + f"export const SEMANTIC_TAGS = [{tags}] as const;\n\n"
        + "export type SemanticTag = (typeof SEMANTIC_TAGS)[number];\n\n"
        + f"export const SEMANTIC_TAG_VERSION = {json.dumps(SEMANTIC_TAG_VERSION, ensure_ascii=False)};\n\n"
        + "export const SEMANTIC_TAG_LABELS: Record<SemanticTag, string> = {\n"
        + labels
        + ",\n};\n"
    )


def main() -> int:
    content = render()
    if "--check" in sys.argv:
        stale = [path for path in TARGETS if not path.is_file() or path.read_text(encoding="utf-8") != content]
        if stale:
            print("[FAIL] 以下文件与 Python 词表不一致，请运行 `python tools/gen_semantic_tags.py`：")
            for path in stale:
                print(f"  - {path.relative_to(ROOT)}")
            return 1
        print(f"[OK] {len(TARGETS)} 份生成物与 Python 词表一致（{SEMANTIC_TAG_VERSION}）")
        return 0

    for path in TARGETS:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        print(f"wrote {path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
