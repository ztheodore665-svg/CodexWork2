#!/usr/bin/env python
"""阶段索引一致性检查（阶段 16 / 文档真相源）。

## 它抓什么

`docs/stages/README.md` 是「现在到底做完到哪」的唯一入口。阶段 16 之前它有三类失真：

1. 阶段 7 写着「待开始」，实际已交付 3 个提交；
2. 阶段 8 整行缺失，而 `docs/design/stage8-multi-agent-audit.md` 存在；
3. 目录里有 19 份 `stage*.md`，表里只链接了 14 份（其余用反引号写成不可点的文本）。

这三类失真有个共同点：**人都不会主动去数**。所以交给脚本数。

## 四条规则

- 每个 `[文字](路径.md)` 链接都必须指向真实存在的文件；
- `docs/stages/` 下每个 `stage*.md` 都必须在本表里出现**至少一次**；
- 引用了阶段文件的行，状态列必须含四个词之一：`已验证` / `未验证` / `未开始` / `已关闭`；
- 状态词只允许这四个（防止又出现「✅ 已完成」这种信息量为零的写法）。

退出码：0 通过，1 有违例。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]
STAGES_DIR = ROOT / "docs" / "stages"
INDEX = STAGES_DIR / "README.md"

STATUS_WORDS = ("已验证", "未验证", "未开始", "已关闭")

LINK_RE = re.compile(r"\[[^\]]*\]\(([^)]+\.md)\)")
STAGE_NAME_RE = re.compile(r"stage\d")


def main() -> int:
    if not INDEX.is_file():
        print(f"[FAIL] 索引不存在：{INDEX}")
        return 1

    text = INDEX.read_text(encoding="utf-8")
    problems: list[str] = []

    referenced: set[str] = set()
    for href in LINK_RE.findall(text):
        target = (STAGES_DIR / unquote(href)).resolve()
        if not target.is_file():
            problems.append(f"链接指向不存在的文件：{href}")
            continue
        if target.parent == STAGES_DIR.resolve():
            referenced.add(target.name)

    existing = {path.name for path in STAGES_DIR.glob("stage*.md") if path.is_file()}

    for name in sorted(existing - referenced):
        problems.append(f"目录里有但索引没链接：{name}")

    for name in sorted(referenced - existing):
        problems.append(f"索引链了但目录里没有：{name}（这条通常意味着文件被改名）")

    for line_no, line in enumerate(text.splitlines(), start=1):
        if not line.startswith("|") or not STAGE_NAME_RE.search(line):
            continue
        if not any(word in line for word in STATUS_WORDS):
            problems.append(
                f"第 {line_no} 行缺少合法状态词（只允许 {' / '.join(STATUS_WORDS)}）：{line.strip()[:80]}"
            )

    if problems:
        print(f"[FAIL] 阶段索引有 {len(problems)} 处问题：")
        for item in problems:
            print(f"  - {item}")
        return 1

    print(f"[OK] 阶段索引一致：{len(existing)} 份阶段文档，全部有链接与状态")
    return 0


if __name__ == "__main__":
    sys.exit(main())
