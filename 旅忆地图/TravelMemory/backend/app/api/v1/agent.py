"""Agent 问答接口（阶段 2 W2-3）—— SSE 流式。

## 为什么用 POST + SSE，而不是 EventSource

浏览器的 `EventSource` 只支持 GET，没法带 JSON body。
而一次问答必须传「问题 + 会话 id + 历史」，用 query string 拼既难看又容易被日志记下来。
所以这里用 **POST + `text/event-stream`**，前端用 `fetch` + `ReadableStream` 自己解析。
这也是主流大模型 API（OpenAI / DeepSeek）的做法。

## 三个必须处理的细节

1. **首字可见**：不能等生成完再返回，必须边生成边推 `delta` 事件。
2. **可中断**：客户端断开（用户点"停止"）时，生成器会被关闭；
   每个事件前检查 `request.is_disconnected()`，及时收手，别继续烧 token。
3. **反代缓冲**：Nginx 默认会缓冲响应体，流式会被"攒起来"再一次性吐出。
   加 `X-Accel-Buffering: no` 与 `Cache-Control: no-cache` 关掉它。

## 为什么不用 FastAPI 的 `Depends(get_session)` 拿数据库会话

`StreamingResponse` 的响应体是在依赖清理**之后**才被消费的，
用 `yield` 依赖注入的会话会在流开始前就关闭，流式写入时直接报错。
所以这里在生成器内部 `async with SessionFactory()` 自己管理生命周期。
（这是流式接口最常见的坑之一，不是本项目特有的毛病。）
"""

from __future__ import annotations

import json
import uuid

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from app.agent.memory.service import MemoryService
from app.agent.observability import Trace
from app.agent.prompts.loader import PROMPT_VERSION
from app.agent.runtime import build_agent, resolve_runtime
from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.session import SessionFactory
from app.schemas.agent import ChatMeta, ChatRequest
from app.services.document_service import DocumentService
from app.services.memory_service import SqlMemoryStore
from app.services.user_service import get_or_create_demo_user

logger = get_logger(__name__)

router = APIRouter(prefix="/agent", tags=["agent"])

#: 心跳间隔：长时间没有 token 时发一个注释行，防止中间层因空闲断开连接
KEEPALIVE_EVERY_EVENTS = 20


def _sse(event: str, data: object) -> bytes:
    """拼一条 SSE 消息：`event:` + `data:` + 空行结束。"""
    payload = json.dumps(data, ensure_ascii=False)
    return f"event: {event}\ndata: {payload}\n\n".encode()


@router.post("/chat", summary="流式问答（SSE）")
async def chat(request: Request, body: ChatRequest) -> StreamingResponse:
    """跑一次问答，以 SSE 逐段返回结果。

    事件序列：`meta` → `parsed` → `evidence` → `delta`* → (`restart` → `delta`*) → `done`
    """
    settings = get_settings()
    session_id = body.session_id or f"web-{uuid.uuid4().hex[:8]}"

    async def event_stream():  # noqa: ANN202 - 生成器类型在流式场景里写不干净
        async with SessionFactory() as session:
            user = await get_or_create_demo_user(session)
            searcher = DocumentService(session)
            memory = (
                MemoryService.from_settings(SqlMemoryStore(session))
                if settings.memory_enabled
                else None
            )
            # 阶段 4 W4-2：AGENT_RUNTIME=graph 时这里拿到的是 LangGraphAgent，
            # 两种运行时对外接口一致，所以下面这段流式代码**不需要任何分支**
            runner = build_agent(searcher, memory=memory)
            trace = Trace(session=session, user_id=user.id, session_id=session_id, query=body.query)

            yield _sse(
                "meta",
                ChatMeta(
                    prompt_version=PROMPT_VERSION,
                    embedding_model=settings.embedding_model_version,
                    runtime=resolve_runtime(settings),
                    session_id=session_id,
                ).model_dump(),
            )

            emitted = 0
            try:
                async for event in runner.run_stream(
                    user_id=user.id,
                    session_id=session_id,
                    query=body.query,
                    history=body.history,
                    trace=trace,
                ):
                    # 用户点了"停止"：立刻收手，别继续烧 token。
                    # 这里必须显式收尾 trace —— break 会让 run_stream 的生成器停在
                    # yield 处，它的 try/except 不会执行，Run 行会永远停在 running。
                    if await request.is_disconnected():
                        logger.info("客户端已断开，终止生成：session=%s", session_id)
                        await trace.finish_run(status="interrupted")
                        break
                    yield _sse(event["type"], event.get("payload", event.get("text", "")))
                    emitted += 1
                    if emitted % KEEPALIVE_EVERY_EVENTS == 0:
                        yield b": keepalive\n\n"
                await session.commit()
            except Exception as exc:  # noqa: BLE001 - 流式接口要把错误变成事件
                logger.exception("流式问答失败")
                yield _sse("error", {"message": str(exc)})

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            # 关掉 Nginx 的响应缓冲，否则流式会被攒成一整块
            "X-Accel-Buffering": "no",
        },
    )
