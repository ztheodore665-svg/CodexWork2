"""LLM 调用封装。

职责边界（设计文档 §5.1）：本模块只负责「调用 + 重试 + 超时 + token 与成本统计」，
**不含任何 prompt 内容**（prompt 全部在 app/agent/prompts/ 下版本化管理）。

provider：
  - openai： OpenAI 兼容的 `/chat/completions`（DeepSeek / Qwen / 火山等均可用）
  - mock：   本地确定性应答，用于在无密钥时验证链路与跑单元测试

`purpose` 参数：标明这次调用属于管道的哪个环节，既便于 trace 归因，
也让 mock 能针对不同环节返回正确形状的结果。
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx

from app.core.cache import (
    TTL_BUDGET_SEC,
    CacheClient,
    NullCache,
    build_cache,
    lock_key,
)
from app.core.config import Settings, get_settings
from app.core.exceptions import LLMBudgetExceeded, LLMError
from app.core.feature_flags import is_enabled
from app.core.logging import get_logger

logger = get_logger(__name__)

#: 价格估算表（元 / 百万 token）：(输入价, 输出价)
#: 仅用于判断成本量级是否失控，不作为计费依据；厂商调价不必同步，未知模型按 0 计并告警。
#:
#: 数值来源：阿里云百炼/PAI 官方价目页（2026-10 核对）。
#: 注意 qwen 系是**阶梯计价**（单价随单次请求输入量跳档），这里取最常用的低档位，
#: 只用于判断"这次跑批花了多少钱量级"，不要拿它去对账。
_PRICE_PER_MTOKENS: dict[str, tuple[float, float]] = {
    "deepseek-chat": (2.0, 8.0),
    "deepseek-reasoner": (4.0, 16.0),
    "qwen-plus": (0.8, 2.0),
    "qwen-max": (20.0, 60.0),
    # ---- 阶段 3 起真实接入的模型（2026-10-22 补，之前一直报"不在价格表"）----
    "qwen3.7-plus": (0.40, 1.60),
    "qwen3.7-text-embedding-flash": (0.125, 0.0),  # 向量模型只按输入计费
}

#: 需要**显式关闭思考模式**的模型名特征（小写匹配）。
#:
#: 为什么必须关：Qwen3 系默认开启 Thinking，会在正式回答前先输出一大段推理过程。
#: 表现为两件事：首字延迟从 ~2s 涨到 14s，以及 **Output Token 被放大好几倍** ——
#: 而输出单价是输入的 4 倍（qwen3.7-plus：输入 0.40 / 输出 1.60 元每百万）。
#: 一次评测 104K Token 的账单，大头就是这段没人看的思考链。
#:
#: 只对 qwen 系发送这个参数：其他 OpenAI 兼容端点遇到未知字段可能直接 400，
#: 而"关思考"是 qwen 特有的开关，不该强加给所有 provider。
_THINKING_OFF_MODEL_HINTS: tuple[str, ...] = ("qwen",)


@dataclass
class LLMMessage:
    role: str  # system / user / assistant
    content: str


def _disable_thinking(payload: dict[str, Any], model: str) -> dict[str, Any]:
    """对 Qwen 系模型强制关闭思考模式（任务一：省钱 + 降延迟）。

    为什么放在请求体顶层而不是 `extra_body`：本项目走的是 OpenAI 兼容端点
    （`/chat/completions`），百炼的兼容模式支持在请求体里直接带 `enable_thinking`；
    再包一层 `extra_body` 反而可能被某些网关原样透传成一个奇怪的嵌套字段。

    **只对 qwen 系发送**：其他 provider 可能对未知字段返回 400。
    """
    if any(hint in model.lower() for hint in _THINKING_OFF_MODEL_HINTS):
        payload["enable_thinking"] = False
    return payload


@dataclass
class LLMResult:
    """一次 LLM 调用的完整结果，成本与 token 都在这一层统计。"""

    content: str
    model: str
    provider: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: int = 0
    cost_cents: float = 0.0
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


@dataclass
class StreamUsage:
    """流式调用的用量。

    为什么不用返回值：异步生成器 `return` 的值拿不到（会被塞进 StopIteration，
    而 `async for` 直接丢弃）。所以用一个可变对象**传进去**，由子类边跑边填。
    """

    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_cents: float = 0.0
    latency_ms: int = 0

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


class BudgetGuard:
    """日预算守卫（阶段 3 W3-3 用途 ④：计数原子化）。

    ## 为什么必须搬到 Redis

    阶段 0 的进程内计数在**单进程**下是对的。但只要起第二个实例（或 uvicorn 多 worker），
    每个进程各有一份 `spent_cents`，实际花费可以是上限的 N 倍 —— 而预算的作用恰恰是
    "在最坏情况下兜住成本"，它自己不能是最不靠谱的那个数。

    ## 为什么存毫分（millicents）而不是分

    Redis 的 `INCR` 只支持整数，而成本是小数（0.0034 分这种量级）。
    存 `cents × 1000` 取整，既保住了原子性，精度也远超预算本身需要的量级
    （0.001 分 = 十万分之一元）。

    ## 为什么缓存不可用时要退回进程内计数而不是"放行"

    `cache.incr()` 在降级实现里会返回 0（含义是"没有计数依据"）。
    如果把它当成"花了 0 分"直接放行，预算就等于失效了。
    所以 `check()` 先 `ping()`：不通就退回本地计数 —— 宁可计数偏保守，也不能算不清。
    """

    #: 1 分 = 1000 毫分（Redis INCR 只认整数，成本却是小数）
    MILLI_PER_CENT: int = 1000

    def __init__(self, limit_cents: int, cache: CacheClient | None = None) -> None:
        self.limit_cents = limit_cents
        self.cache = cache or NullCache()
        #: 缓存不可用时的兜底计数（进程内）
        self.local_milli: int = 0

    @property
    def _day_key(self) -> str:
        return lock_key("budget_llm", datetime.now(UTC).strftime("%Y-%m-%d"))

    async def spent_cents(self) -> float:
        """当日已花费（分）。"""
        if await self._cache_alive():
            return await self._remote_milli() / self.MILLI_PER_CENT
        return self.local_milli / self.MILLI_PER_CENT

    async def check(self) -> None:
        if self.limit_cents <= 0:
            return
        spent = await self.spent_cents()
        if spent >= self.limit_cents:
            raise LLMBudgetExceeded(
                f"已用 {spent:.2f} 分 / 上限 {self.limit_cents} 分（LLM_DAILY_BUDGET_CENTS）"
            )

    async def add(self, cents: float) -> None:
        milli = int(round(cents * self.MILLI_PER_CENT))
        if milli <= 0:
            return
        if await self._cache_alive():
            # 递增与设过期不是原子的：并发下可能重复 EXPIRE，但那没有副作用；
            # 反过来（先设过期再递增）若中途崩溃会留下一个永不过期的 key。
            await self.cache.incr(self._day_key, milli, ttl_sec=TTL_BUDGET_SEC)
            return
        self.local_milli += milli

    async def _cache_alive(self) -> bool:
        try:
            return await self.cache.ping()
        except Exception:  # noqa: BLE001 - 探测就是要吞掉所有异常
            return False

    async def _remote_milli(self) -> int:
        raw = await self.cache.get(self._day_key)
        try:
            return int(raw) if raw is not None else 0
        except (TypeError, ValueError):
            logger.warning("预算计数不是整数（%r），按 0 处理", raw)
            return 0


def _budget_cache(settings: Settings) -> CacheClient:
    """预算计数用的缓存。`cache` 开关关闭时明确用 NullCache（= 退回进程内计数）。

    这里不做"REDIS_URL 有值就连"的隐式判断：开关是唯一真相，
    否则同一份代码在不同机器上走不同分支，预算对不上时根本没法排查。
    """
    if not is_enabled("cache", settings):
        return NullCache()
    try:
        return build_cache(settings)
    except Exception as exc:  # noqa: BLE001 - 缓存装不起来就退回本地计数
        logger.warning("预算缓存不可用，退回进程内计数：%s", exc)
        return NullCache()


class BaseLLM(ABC):
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.budget = BudgetGuard(settings.llm_daily_budget_cents, _budget_cache(settings))
        #: 最近一次流式调用的用量（供调用方写 trace / 统计）
        self.last_stream_usage: StreamUsage | None = None

    @property
    @abstractmethod
    def provider(self) -> str: ...

    @property
    @abstractmethod
    def model(self) -> str: ...

    @abstractmethod
    async def _completion(
        self,
        messages: list[LLMMessage],
        *,
        purpose: str,
        temperature: float,
        max_tokens: int,
        json_mode: bool,
    ) -> LLMResult:
        """子类实现真正的调用。`purpose` 标明调用来自管道的哪个环节。"""

    @abstractmethod
    def _astream_completion(
        self,
        messages: list[LLMMessage],
        *,
        purpose: str,
        temperature: float,
        max_tokens: int,
        usage: StreamUsage,
    ) -> AsyncGenerator[str, None]:
        """子类实现流式调用，逐段 yield 文本，用量写进 `usage`。

        基类这里刻意 **不写 yield**：写了就成了"生成器函数"，mypy 会要求返回类型
        是 Generator 的父类型；而抽象方法只需要声明"子类要返回一个异步生成器"。
        """
        raise NotImplementedError

    async def chat(
        self,
        messages: list[LLMMessage],
        *,
        purpose: str = "generate",
        temperature: float | None = None,
        max_tokens: int | None = None,
        json_mode: bool = False,
    ) -> LLMResult:
        """统一入口：先过预算闸门，再调用，最后统计成本。"""
        await self.budget.check()
        result = await self._completion(
            messages,
            purpose=purpose,
            temperature=self.settings.llm_temperature if temperature is None else temperature,
            max_tokens=max_tokens or self.settings.llm_max_tokens,
            json_mode=json_mode,
        )
        result.cost_cents = self.estimate_cost(result.prompt_tokens, result.completion_tokens)
        await self.budget.add(result.cost_cents)
        logger.debug(
            "llm.call purpose=%s model=%s tokens=%d cost=%.3f分 latency=%dms",
            purpose,
            result.model,
            result.total_tokens,
            result.cost_cents,
            result.latency_ms,
        )
        return result

    async def astream_chat(
        self,
        messages: list[LLMMessage],
        *,
        purpose: str = "generate",
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> AsyncGenerator[str, None]:
        """流式入口（阶段 2 W2-3 的 SSE 依赖它）：逐段 yield 文本。

        预算闸门在**开始前**检查，成本在**结束后**结算 —— 与 `chat` 完全一致，
        避免流式路径绕过成本控制（这种"旁路漏算"是成本失控的典型原因）。
        """
        await self.budget.check()
        usage = StreamUsage()
        started = time.perf_counter()
        async for chunk in self._astream_completion(
            messages,
            purpose=purpose,
            temperature=self.settings.llm_temperature if temperature is None else temperature,
            max_tokens=max_tokens or self.settings.llm_max_tokens,
            usage=usage,
        ):
            yield chunk
        usage.latency_ms = int((time.perf_counter() - started) * 1000)
        usage.cost_cents = self.estimate_cost(usage.prompt_tokens, usage.completion_tokens)
        await self.budget.add(usage.cost_cents)
        self.last_stream_usage = usage

    def estimate_cost(self, prompt_tokens: int, completion_tokens: int) -> float:
        """按价格表估算成本（单位：分）。未知模型记 0 并告警，避免静默算错钱。"""
        price = _PRICE_PER_MTOKENS.get(self.model)
        if price is None:
            logger.warning("模型 %s 不在价格表中，本次调用成本按 0 计", self.model)
            return 0.0
        in_rate, out_rate = price
        yuan = prompt_tokens / 1_000_000 * in_rate + completion_tokens / 1_000_000 * out_rate
        return round(yuan * 100, 4)


class OpenAICompatLLM(BaseLLM):
    """OpenAI 兼容协议的 LLM。

    重试策略：只对「网络超时 / 连接错误 / 5xx / 429」重试，业务 4xx（如参数错误）直接失败，
    避免把明显错误的请求重复烧钱。
    """

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self._base_url = settings.llm_base_url.rstrip("/")

    @property
    def provider(self) -> str:
        return "openai"

    @property
    def model(self) -> str:
        return self.settings.llm_model

    async def _completion(
        self,
        messages: list[LLMMessage],
        *,
        purpose: str,
        temperature: float,
        max_tokens: int,
        json_mode: bool,
    ) -> LLMResult:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }
        _disable_thinking(payload, self.model)
        if json_mode:
            # 大部分兼容端点支持该参数；不支持时模型通常会忽略，下游 JSON 解析会兜底
            payload["response_format"] = {"type": "json_object"}

        headers = {"Authorization": f"Bearer {self.settings.llm_api_key}"}
        last_error: str = ""
        for attempt in range(self.settings.llm_max_retries + 1):
            started = time.perf_counter()
            try:
                async with httpx.AsyncClient(
                    base_url=self._base_url, headers=headers, timeout=self.settings.llm_timeout_sec
                ) as client:
                    resp = await client.post("/chat/completions", json=payload)
            except (httpx.TimeoutException, httpx.ConnectError) as exc:
                last_error = f"网络错误: {exc}"
                if attempt < self.settings.llm_max_retries:
                    await asyncio.sleep(1.5**attempt)
                    continue
                raise LLMError(last_error, retryable=True) from exc

            latency_ms = int((time.perf_counter() - started) * 1000)

            if resp.status_code == 429 or resp.status_code >= 500:
                last_error = f"status={resp.status_code} body={resp.text[:200]}"
                if attempt < self.settings.llm_max_retries:
                    await asyncio.sleep(1.5**attempt)
                    continue
                raise LLMError(last_error, retryable=True)

            if resp.status_code != 200:
                # 4xx：多半是请求本身有问题，重试无意义
                raise LLMError(
                    f"LLM 返回错误 status={resp.status_code} body={resp.text[:300]}",
                    retryable=False,
                )

            body = resp.json()
            try:
                choice = body["choices"][0]
                content = choice["message"]["content"] or ""
            except (KeyError, IndexError) as exc:
                raise LLMError(f"LLM 响应结构异常: {body!r:.300}") from exc

            usage = body.get("usage") or {}
            return LLMResult(
                content=content.strip(),
                model=body.get("model", self.model),
                provider=self.provider,
                prompt_tokens=int(usage.get("prompt_tokens", 0)),
                completion_tokens=int(usage.get("completion_tokens", 0)),
                latency_ms=latency_ms,
                raw=body,
            )

        raise LLMError(last_error or "LLM 调用失败", retryable=True)

    async def _astream_completion(
        self,
        messages: list[LLMMessage],
        *,
        purpose: str,
        temperature: float,
        max_tokens: int,
        usage: StreamUsage,
    ) -> AsyncGenerator[str, None]:
        """SSE 流式：逐行读 `data: {...}`，取 `choices[0].delta.content`。

        `stream_options.include_usage` 让服务端在最后一个 chunk 带上 token 用量 ——
        不带上它，流式调用就统计不到真实用量，成本账会一直少算。
        """
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        _disable_thinking(payload, self.model)
        headers = {"Authorization": f"Bearer {self.settings.llm_api_key}"}
        async with (
            httpx.AsyncClient(
                base_url=self._base_url, headers=headers, timeout=self.settings.llm_timeout_sec
            ) as client,
            client.stream("POST", "/chat/completions", json=payload) as resp,
        ):
            if resp.status_code != 200:
                raise LLMError(
                    f"LLM 流式返回错误 status={resp.status_code} body={resp.text[:300]}",
                    retryable=False,
                )
            async for line in resp.aiter_lines():
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if not data or data == "[DONE]":
                    continue
                try:
                    body = json.loads(data)
                except json.JSONDecodeError:
                    continue
                if body.get("usage"):
                    usage.prompt_tokens = int(body["usage"].get("prompt_tokens", 0))
                    usage.completion_tokens = int(body["usage"].get("completion_tokens", 0))
                choices = body.get("choices") or []
                if not choices:
                    continue
                delta = (choices[0].get("delta") or {}).get("content")
                if delta:
                    yield delta


class MockLLM(BaseLLM):
    """确定性本地应答，用于「无密钥也能跑通链路」与单元测试。

    它做的事：从上下文中抽出编号证据 `[n]`，拼成一个必定带引用的回答。
    因此整条管道的引用率检查、拒答、trace 都能在没有外部依赖的情况下被验证。
    """

    @property
    def provider(self) -> str:
        return "mock"

    @property
    def model(self) -> str:
        return "mock-llm-v0"

    async def _completion(
        self,
        messages: list[LLMMessage],
        *,
        purpose: str,
        temperature: float,
        max_tokens: int,
        json_mode: bool,
    ) -> LLMResult:
        last_user = next((m.content for m in reversed(messages) if m.role == "user"), "")
        content = self._route(purpose, last_user, json_mode)
        # token 数按字符粗估，只为让 trace/成本的量级合理
        return LLMResult(
            content=content,
            model=self.model,
            provider=self.provider,
            prompt_tokens=max(1, len(last_user) // 2),
            completion_tokens=max(1, len(content) // 2),
            latency_ms=1,
        )

    async def _astream_completion(
        self,
        messages: list[LLMMessage],
        *,
        purpose: str,
        temperature: float,
        max_tokens: int,
        usage: StreamUsage,
    ) -> AsyncGenerator[str, None]:
        """本地流式：把答案切成小段，段间睡一小会儿。

        睡这一段不是装饰 —— **没有它就无法验证"首字可见"**：
        一次性返回和流式返回在前端看起来一模一样，
        只有让 token 之间真的有时间差，才能确认前端是在逐字渲染而不是等完整响应。
        """
        last_user = next((m.content for m in reversed(messages) if m.role == "user"), "")
        content = self._route(purpose, last_user, False)
        usage.prompt_tokens = max(1, len(last_user) // 2)
        usage.completion_tokens = max(1, len(content) // 2)

        size = self.settings.mock_stream_chunk_chars
        for start in range(0, len(content), size):
            yield content[start : start + size]
            await asyncio.sleep(self.settings.mock_stream_delay_sec)

    def _route(self, purpose: str, user_content: str, json_mode: bool) -> str:
        """按调用环节分派应答（确定性，不调用任何外部服务）。"""
        if purpose == "query_parse" or json_mode:
            return self._json_answer(user_content)
        if purpose == "rewrite":
            return self._rewrite_answer(user_content)
        if purpose == "generate":
            return self._cited_answer(user_content)
        # 兜底：留给后续阶段新增的环节
        return "[MOCK] 本地占位回答。"

    @staticmethod
    def _rewrite_answer(user_content: str) -> str:
        """rewriter：直接回用户的原始提问（去空白）。"""
        lines = [ln.strip() for ln in user_content.splitlines() if ln.strip()]
        for ln in lines:
            if ln.startswith("用户问题：") or ln.startswith("原始提问："):
                return ln.split("：", 1)[1].strip()
        return lines[-1] if lines else ""

    @staticmethod
    def _json_answer(user_content: str) -> str:
        """query_parse：抽关键词 + 从内置词表里认地名与主题。

        两个刻意的设计：
          1. **只读「提问：」那一行** —— 否则会把 prompt 里的字段说明、示例 JSON
             全都当成关键词，检索串会被污染成一大段废话；
          2. **不猜 `time_range`** —— 日历换算交给 `agent/rag/time_parser.py`。
             mock 负责"认字"，规则负责"算数"，这条分工在本项目里是硬性的。
        """
        query = _last_query_line(user_content)
        keywords = _mock_keywords(query)
        places = [w for w in _MOCK_PLACES if w in query]
        themes = [w for w in _MOCK_THEMES if w in query]
        return json.dumps(
            {"keywords": keywords, "places": places, "themes": themes, "time_range": None},
            ensure_ascii=False,
        )

    @staticmethod
    def _cited_answer(user_content: str) -> str:
        """generate：把前 3 条证据的首句改写出来，并强制带上 [n]。"""
        lines = user_content.splitlines()
        picks: list[tuple[str, str]] = []
        for line in lines:
            m = re.match(r"^\[(\d+)\]\s*(.+)$", line.strip())
            if m:
                picks.append((m.group(1), m.group(2).strip()))
            if len(picks) >= 3:
                break
        if not picks:
            return "抱歉，在你的照片与行程记录里没有找到相关证据，我无法回答这个问题。"
        sentences = []
        for idx, content in picks:
            first_sentence = re.split(r"[。！？!?；;]", content)[0].strip()
            sentences.append(f"{first_sentence}[{idx}]")
        return "；".join(sentences) + "。"


#: mock 用的极简词表。只服务于"无密钥时也能验证链路"，
#: 真实场景一律由 LLM 抽取；放在这里是为了避免 core 层反向依赖 agent 层。
_MOCK_PLACES = (
    "杭州",
    "乌镇",
    "西塘",
    "洛阳",
    "张家界",
    "苏州",
    "上海",
    "哈尔滨",
    "成都",
    "厦门",
    "西安",
    "青海湖",
    "鼓浪屿",
    "西湖",
    "外滩",
    "敦煌",
    "西安城墙",
)
_MOCK_THEMES = (
    "古镇",
    "水乡",
    "自然风光",
    "人文历史",
    "博物馆",
    "美食",
    "夜景",
    "海岛",
    "园林",
    "都市风光",
    "冰雪",
    "湖泊",
    "山岳",
    "休闲度假",
    "寺庙",
    "古迹",
)

#: 极简停用字表。mock 没有分词器，只能靠这些字把"年去过巴黎吗"切成"巴黎"。
#: 注意这只是为了让离线链路的关键词**像点样**，不追求分词准确性。
_MOCK_STOPCHARS = "的了着过吗呢吧啊去是在有我都还就也很最把被给对从到和跟与"

#: 切词后残留的无意义前缀（"年秋天" → "秋天"）
_MOCK_LEAD_JUNK = "年月日在"


def _mock_keywords(query: str) -> list[str]:
    """mock 分词：按停用字切分连续汉字串，再去掉残留的无意义前缀。"""
    words: list[str] = []
    for run in re.findall(r"[\u4e00-\u9fa5]{2,}", query):
        for segment in re.split(rf"[{_MOCK_STOPCHARS}]", run):
            segment = segment.lstrip(_MOCK_LEAD_JUNK)
            if len(segment) >= 2:
                words.append(segment)
    return list(dict.fromkeys(words))[:8]


def _last_query_line(text: str) -> str:
    """取 prompt 里最后一行以「提问：」开头的内容（mock 专用）。

    prompt 模板尾部固定是 `提问：{{query}}`，取最后一个匹配即可避开前面的示例。
    """
    for line in reversed(text.splitlines()):
        if line.strip().startswith("提问："):
            return line.split("：", 1)[1].strip()
    return text.strip()


def build_llm(settings: Settings | None = None) -> BaseLLM:
    """工厂：按配置选择 LLM 实现（auto = 有 key 走真实，无 key 走 mock）。"""
    settings = settings or get_settings()
    provider = settings.llm_provider.lower()

    if provider == "auto":
        provider = "openai" if settings.llm_key_present else "mock"

    if provider == "openai":
        return OpenAICompatLLM(settings)
    if provider == "mock":
        logger.warning("LLM_PROVIDER=mock：未配置 LLM_API_KEY，回答由本地规则生成，仅用于验证链路")
        return MockLLM(settings)

    raise LLMError(f"未知的 llm provider: {settings.llm_provider}")
