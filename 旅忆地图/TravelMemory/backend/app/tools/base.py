"""工具框架基座（阶段 3 W3-1）。

## 为什么需要一层框架，而不是直接写两个函数

工具调用看起来只是"调个 HTTP"，但工程上它有五件**每个工具都要重复一遍**的事。
只要漏一件，就会以最难查的方式出问题：

| 必做项 | 漏掉之后的症状 |
| --- | --- |
| 入参用 Pydantic 校验 | LLM 填出来的参数缺字段 / 类型错，把脏参数直接打给外部 API |
| 超时 | 外部 API 卡住时，整条问答被拖到前端超时（P95 首字 < 3s 直接破线） |
| 有限重试 | 一次网络抖动就让工具"不可用"，用户看到的是记忆检索的降级答案 |
| 缓存 | 同一批坐标反复转换，配额烧光、命中率永远达不到 60% |
| 调用日志 | "这个数字是哪来的"变成悬案，缓存命中率指标无法统计 |

框架的价值就是让**工具作者只写业务**（`_run` 里的那几行），上面五件一次写对。

## 关键取舍：失败降级，绝不抛错

`BaseTool.run()` **永不向外抛异常**（Pydantic 校验失败、超时、重试耗尽、业务异常，
一律变成 `ToolResult(ok=False, degraded=True)`）。依据是 PROJECT.md §二、[8]：

> 扩展模块走 feature flag，默认全关；**失败必须降级而非抛错**

工具是主流程的旁路设施。它挂了应该让 Agent 退回"只用记忆回答"，
而不是让整条问答 500。

## 依赖方向（PROJECT.md §二、[4]）

`app/tools/` 只允许 import `app/core/`、`app/services/`、`app/models/`、`app/utils/`。
**禁止 import `app/agent/` 与 `app/api/`** —— `tests/test_layering.py` 在守这条线。
工具不认识管道，管道认识工具；方向反了就会出现"工具偷偷改 state"这类不可控行为。
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, TypeVar, cast

from pydantic import BaseModel, ValidationError

from app.core.cache import CacheClient, NullCache, api_cache_key
from app.core.logging import get_logger

logger = get_logger(__name__)

InputT = TypeVar("InputT", bound=BaseModel)


@dataclass
class ToolResult:
    """工具执行结果。**调用方只看 `ok`，不接异常**。

    `degraded` 与 `ok=False` 的区别值得说清：

    - `ok=True`：拿到了可用结果（可能来自缓存，见 `cache_hit`）
    - `ok=False, degraded=True`：**工具没帮上忙，但流程可以继续** —— 调用方应当
      退回记忆检索，并在 prompt 里声明"地图工具本次不可用"
    - `ok=False, degraded=False`：入参就不合法（LLM 填错了），
      这种**不该重试**，重试只会用同样的脏参数再打一次外部 API

    `cache_hit` 是必填字段而不是可选项：缓存命中率 ≥ 60% 是设计文档 §10.1 的验收指标，
    每一行调用都必须能回答"这次命中了吗"，缺一行统计就偏一点。
    """

    tool: str
    ok: bool
    data: dict[str, Any] = field(default_factory=dict)
    error: str = ""
    degraded: bool = False
    cache_hit: bool = False
    latency_ms: int = 0
    #: 入参合法性：非法入参不重试（retryable=False）
    retryable: bool = True

    @property
    def text(self) -> str:
        """给证据块用的一句话事实描述。失败时返回空串（空串不会被拼进上下文）。"""
        return str(self.data.get("text") or "")


@dataclass
class ToolContext:
    """工具能看到的外部世界。**刻意做得很窄**：只有缓存与设置。

    不把 `PipelineState` 传进来是有意的：工具一旦能改状态，
    "某条证据是哪个环节加进去的"就再也说不清了。
    """

    cache: CacheClient = field(default_factory=NullCache)
    timeout_sec: float = 8.0
    max_retries: int = 1
    #: 是否启用结果缓存（关掉 = 每次都真打外部 API，排障用）
    use_cache: bool = True


class BaseTool[InputT]:
    """工具基类。子类只需声明 `name` / `description` / `input_model` 并实现 `_run`。

    模板方法顺序（每一步都可被子类覆盖，但默认行为是有讲究的）：

    ```
    validate(入参) → cache.get → [miss] _run(带超时+重试) → cache.set → 返回结果
    ```

    缓存放在重试**外面**：重试是为了扛网络抖动，命中缓存时根本没有网络调用，
    也就没有"重试"可言。反过来（重试在外）会让一次抖动放大成 N 倍配额消耗。
    """

    name: str = ""
    description: str = ""
    input_model: type[BaseModel] | None = None
    #: 缓存命名空间（① 外部 API 结果）。不设表示这个工具不缓存
    cache_namespace: str = ""
    cache_ttl_sec: int = 0

    def __init__(self, context: ToolContext | None = None) -> None:
        self.context = context or ToolContext()

    # ---------------------------------------------------------------- 入口
    async def run(self, payload: dict[str, Any]) -> ToolResult:
        """执行工具。**此函数保证不抛异常**。"""
        started = time.perf_counter()
        result = await self._run_guarded(payload)
        result.latency_ms = int((time.perf_counter() - started) * 1000)
        return result

    # ---------------------------------------------------------------- 内部
    async def _run_guarded(self, payload: dict[str, Any]) -> ToolResult:
        validated = self._validate(payload)
        if validated is None:
            # 入参不合法：LLM 填错了。不重试 —— 同样的脏参数重试没有意义
            return ToolResult(
                tool=self.name,
                ok=False,
                error="入参校验失败",
                degraded=True,
                retryable=False,
            )

        key = self._cache_key(payload)
        if self.context.use_cache and key:
            cached = await self._cache_get(key)
            if cached is not None:
                return ToolResult(
                    tool=self.name, ok=True, data=cached, cache_hit=True, degraded=False
                )

        data, error, retryable = await self._execute(validated)
        if error:
            return ToolResult(
                tool=self.name, ok=False, error=error, degraded=True, retryable=retryable
            )

        if self.context.use_cache and key:
            await self._cache_set(key, data)
        return ToolResult(tool=self.name, ok=True, data=data)

    def _validate(self, payload: dict[str, Any]) -> InputT | None:
        if self.input_model is None:
            return None
        try:
            return cast(InputT, self.input_model.model_validate(payload))
        except ValidationError as exc:
            logger.warning("工具 %s 入参非法：%s", self.name, exc.errors()[:2])
            return None

    async def _execute(self, validated: InputT) -> tuple[dict, str, bool]:
        """带超时与有限重试地执行 `_run`。返回 `(data, error, retryable)`。"""
        attempts = max(1, int(self.context.max_retries) + 1)
        last_error = ""
        for attempt in range(attempts):
            try:
                data = await asyncio.wait_for(
                    self._run(validated), timeout=self.context.timeout_sec
                )
                return data, "", True
            except TimeoutError:
                last_error = f"超时（>{self.context.timeout_sec}s）"
            except Exception as exc:  # noqa: BLE001 - 工具异常一律降级
                last_error = f"{type(exc).__name__}: {exc}"
                # 业务异常重试没有意义（参数没问题，是对方不返回结果）
                if not _is_transient(exc):
                    break
            if attempt + 1 < attempts:
                logger.info("工具 %s 第 %d 次失败，重试：%s", self.name, attempt + 1, last_error)
        logger.warning("工具 %s 执行失败：%s", self.name, last_error)
        return {}, last_error, True

    async def _run(self, validated: InputT) -> dict[str, Any]:  # pragma: no cover - 抽象
        """子类实现真正的业务。返回的结果必须是可 JSON 序列化的 dict。"""
        raise NotImplementedError

    # ---------------------------------------------------------------- 缓存
    def _cache_key(self, payload: dict[str, Any]) -> str:
        if not self.cache_namespace:
            return ""
        return api_cache_key(self.cache_namespace, payload)

    async def _cache_get(self, key: str) -> dict[str, Any] | None:
        try:
            raw = await self.context.cache.get(key)
        except Exception as exc:  # noqa: BLE001 - 缓存是旁路，读失败当作 miss
            logger.warning("缓存读取失败，按 miss 处理：%s", exc)
            return None
        if raw is None:
            return None
        try:
            import json

            loaded: dict[str, Any] = json.loads(raw)
            return loaded
        except Exception:  # noqa: BLE001 - 脏缓存按 miss 处理，不能直接崩
            logger.warning("缓存内容无法解析，按 miss 处理：%s", key)
            return None

    async def _cache_set(self, key: str, data: dict[str, Any]) -> None:
        try:
            import json

            await self.context.cache.set(
                key, json.dumps(data, ensure_ascii=False), self.cache_ttl_sec
            )
        except Exception as exc:  # noqa: BLE001 - 写缓存失败不影响本次结果
            logger.warning("缓存写入失败（不影响本次结果）：%s", exc)

    # ---------------------------------------------------------------- 描述
    def schema(self) -> dict[str, Any]:
        """给 LLM 看的工具描述（工具选择用）。"""
        return {
            "name": self.name,
            "description": self.description,
            "parameters": (self.input_model.model_json_schema() if self.input_model else {}),
        }


def _is_transient(exc: BaseException) -> bool:
    """判断异常是否值得重试。

    只有**网络类**异常重试才有意义：超时、连接错误。业务异常（对方返回错误码、
    参数不被接受）重试只会再错一次，还白白多烧一次配额。
    """
    return isinstance(exc, (TimeoutError, asyncio.TimeoutError, ConnectionError, OSError))


#: 供单测替换 `_run` 的钩子类型（避免测试去 patch 私有方法）
RunFn = Callable[[BaseModel], Awaitable[dict[str, Any]]]
