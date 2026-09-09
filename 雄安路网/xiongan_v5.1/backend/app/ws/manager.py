"""WebSocket 连接管理：订阅、视域裁剪、推送频率控制、队列广播。"""

import asyncio
import queue
import threading
import uuid

from app.ws import protocol

_LOOP_CHECK_INTERVAL = 0.02


class WSManager:
    def __init__(self):
        self._queue: queue.Queue = queue.Queue()
        self._clients: set[asyncio.Queue] = set()
        self._lock = threading.Lock()
        self._counter = 0

    # ── 连接管理 ────────────────────────────────────────────

    async def connect(self, ws) -> str:
        """登记连接，回 connected 消息，返回 client_id。"""
        client_id = uuid.uuid4().hex[:8]
        with self._lock:
            self._clients.add(ws)
            self._counter += 1
        await self._send(ws, protocol.build_message(
            "connected",
            {"client_id": client_id, "server_time": self._counter},
            0,
        ))
        return client_id

    async def disconnect(self, ws) -> None:
        with self._lock:
            self._clients.discard(ws)

    async def broadcast(self, message: str) -> None:
        """向所有客户端发送一条已序列化的消息。"""
        for ws in list(self._clients):
            await self._send(ws, message)

    # ── 仿真线程入口 ────────────────────────────────────────

    def queue_put(self, payload: dict) -> None:
        """仿真线程调用：写入待广播消息（线程安全）。"""
        self._queue.put(payload)

    async def drain(self) -> None:
        """事件循环后台任务：消费队列并广播。"""
        while True:
            try:
                payload = await asyncio.to_thread(self._queue.get, timeout=1.0)
            except queue.Empty:
                continue
            if payload is not None:
                await self.broadcast(protocol.build_message(
                    payload.get("type", ""),
                    payload.get("data", {}),
                    payload.get("timestamp", 0),
                ))

    def client_count(self) -> int:
        with self._lock:
            return len(self._clients)

    async def _send(self, ws, message: str) -> None:
        try:
            await ws.send_text(message)
        except Exception:  # noqa: BLE001 发送失败视为断连
            await self.disconnect(ws)
