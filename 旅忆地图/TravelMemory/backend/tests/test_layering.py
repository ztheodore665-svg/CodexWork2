"""依赖方向守卫（PROJECT.md §二、[4] / E4）。

`agent → nodes → tools → services → models` 这条链**只能单向依赖**。
写成 pytest 而不是 Makefile 里的一句 grep，是为了让它在 Windows 上也能跑、
并且一次红就把「哪个文件、第几行、依赖了谁」全列出来。

为什么用 AST 而不是正则：正则会把注释里提到的 `import app.agent`、字符串常量、
`诊疗app_agent的正确方式` 之类的文本误判成违规。AST 读的是导入语句本身，零误报。

同时它也是 `os.getenv` 红线的守卫（R3-1.5 ③）：
除 `core/config.py` 与 `core/feature_flags.py` 之外，任何模块都不许直接读环境变量。
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]

#: 被守卫的包： → 禁止依赖的上层模块
#:
#: 阶段 16 补了 `schemas` 与 `agent` 两条：之前只守 tools/services/models，
#: 于是 `schemas/semantic.py → services/semantic_trajectory` 这条反向依赖
#: 在 15 个阶段里一直存在却从未被拦下。守卫的价值取决于它覆盖到哪，
#: 只守"已经守住的三个方向"等于没守。
LAYERING_RULES = (
    ("app/tools", ("app.agent", "app.api")),
    ("app/services", ("app.agent", "app.api")),
    ("app/models", ("app.agent", "app.api", "app.tools", "app.services")),
    ("app/schemas", ("app.agent", "app.api", "app.tools", "app.services")),
    ("app/agent", ("app.api",)),
)

#: 允许直接读环境变量的模块（`os.getenv` 的唯一入口）
ENV_READ_ALLOWLIST = {
    "app/core/config.py",
    "app/core/feature_flags.py",
}

#: **已知的历史越界**（键 = (文件, 目标模块)，值 = 成因与关闭方案）。
#: 按 PRD §6.5 止血规则第 4 条，本轮新发现的问题先登记、不在本轮处理：
#: 修复它需要把 `StructuredFilter` 迁出 `agent` 层，属于结构改动，超出 T3-01 的五项排错范围。
#: 注意这里用「精确相等」而不是「允许清单」—— 一旦有人把它修掉，用例会反过来要求删除该例外，
#: 免得白名单变成长期烂账。
KNOWN_EXCEPTIONS: dict[tuple[str, str], str] = {
    ("app/services/document_service.py", "app.agent.rag.filters"): (
        "`StructuredFilter` 是 agent 与 services 共用的数据契约，却被放在 agent 层。"
        "关闭方案：把它迁到 app/schemas/ 下（services 改为向下依赖 schemas，"
        "`rag/filters.py` 保留 re-export 以免牵连调用方），然后删除本条例外。"
        "已登记为延后项 D-11（PRD §6.3 同类处理）"
    ),
}


@dataclass(frozen=True)
class ImportViolation:
    """一次越界依赖：排障要的信息全在字段里。"""

    file: str
    line: int
    target: str

    def __str__(self) -> str:
        return f"{self.file}:{self.line} → {self.target}"

    @property
    def key(self) -> tuple[str, str]:
        return (self.file, self.target)


def iter_python_files(root: Path) -> list[Path]:
    """列出目录下全部 .py（目录不存在 / 为空都返回空列表，而不是抛错）。"""
    if not root.exists():
        return []
    return sorted(path for path in root.rglob("*.py") if path.is_file())


def imported_targets(path: Path) -> list[tuple[int, str]]:
    """取出文件里全部被 import 的目标模块名，带行号。"""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.append((node.lineno, alias.name))
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.append((node.lineno, node.module))
    return found


def scan_illegal_imports(package: str, forbidden: tuple[str, ...]) -> list[ImportViolation]:
    """扫描一个包：凡 import 目标命中 forbidden 的都算越界。

    `app_toolbox` 这种只是前缀撞名的模块不能被误伤，所以匹配后必须再看下一段是否为 `.`。
    """
    violations: list[ImportViolation] = []
    for path in iter_python_files(BACKEND_DIR / package):
        relative = path.relative_to(BACKEND_DIR).as_posix()
        for line, module in imported_targets(path):
            for banned in forbidden:
                if module == banned or module.startswith(f"{banned}."):
                    violations.append(ImportViolation(relative, line, module))
                    break
    return violations


def scan_os_getenv_violations() -> list[ImportViolation]:
    """扫描 `app/**/*.py` 里的 `os.getenv` / `os.environ` 调用。"""
    violations: list[ImportViolation] = []
    for path in iter_python_files(BACKEND_DIR / "app"):
        relative = path.relative_to(BACKEND_DIR).as_posix()
        if relative in ENV_READ_ALLOWLIST:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Attribute):
                continue
            if getattr(node.value, "id", None) == "os" and node.attr in ("getenv", "environ"):
                violations.append(ImportViolation(relative, node.lineno, f"os.{node.attr}"))
    return violations


@pytest.mark.parametrize(("package", "forbidden"), LAYERING_RULES)
def test_package_does_not_depend_upstream(package: str, forbidden: tuple[str, ...]):
    found = scan_illegal_imports(package, forbidden)

    unknown = [item for item in found if item.key not in KNOWN_EXCEPTIONS]
    assert unknown == [], (
        f"{package}/ 反向依赖了上层模块（方向必须是 agent → nodes → tools → services → models）:\n"
        + "\n".join(f"  {item}" for item in unknown)
        + "\n若为历史遗留，请像 KNOWN_EXCEPTIONS 里那样写明成因与关闭方案再登记。"
    )

    known_keys = {item.key for item in found}
    stale = []
    for key, reason in KNOWN_EXCEPTIONS.items():
        if key[0].startswith(f"{package}/") and key not in known_keys:
            stale.append(f"  {key[0]} → {key[1]}（原记载：{reason}）")
    assert stale == [], (
        "以下例外对应的越界 import 已经不存在，请从 KNOWN_EXCEPTIONS 删除（白名单不许留腐味）：\n"
        + "\n".join(stale)
    )


def test_tools_package_exists():
    """`app/tools/` 不再是空目录：它是阶段 3 要填的位置（PRD §6.4），而不是缺陷。

    这条断言存在的意义：空目录不会被 git 跟踪。若哪天有人把 `__init__.py` 删了，
    上面那条 import 扫描会因为"扫不到文件"直接变成永绿 —— 用这条堵上。
    """
    tools_init = BACKEND_DIR / "app" / "tools" / "__init__.py"
    assert tools_init.is_file(), f"app/tools 缺包声明：{tools_init}"


def test_only_config_reads_environment_variables():
    """环境变量只在 core/config.py 与 feature_flags.py 读（PROJECT.md §二、[5]）。"""
    violations = scan_os_getenv_violations()
    assert violations == [], (
        "禁止在业务模块直接读环境变量（一律走 core/config.py / feature_flags.is_enabled）:\n"
        + "\n".join(f"  {item}" for item in violations)
    )
