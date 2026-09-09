"""V2X 传输层抽象：内存模拟（默认）与 socket 扩展占位。

云-边-车三端之间的所有消息统一经 Transport 收发，便于统计通信量与延迟，
并可在演示彩蛋中无缝替换为真多机 SocketTransport。
"""

import random
from collections import defaultdict


class Transport:
    """抽象基类。"""

    def send(self, msg: dict) -> None:
        raise NotImplementedError

    def receive(self, role: str) -> list[dict]:
        raise NotImplementedError

    def stats(self) -> dict:
        return {"sent": 0, "received": 0, "dropped": 0}


class SimulatedTransport(Transport):
    """进程内队列 + 可配延迟/抖动/丢包。"""

    def __init__(self, base_delay: float = 0.01, jitter: float = 0.005,
                 loss_rate: float = 0.0):
        self.base_delay = base_delay
        self.jitter = jitter
        self.loss_rate = loss_rate
        self._queues: dict[str, list[dict]] = defaultdict(list)
        self._sent = 0
        self._received = 0
        self._dropped = 0

    def send(self, msg: dict) -> None:
        self._sent += 1
        if self.loss_rate > 0 and random.random() < self.loss_rate:
            self._dropped += 1
            return
        delay = max(0.0, self.base_delay + random.gauss(0.0, self.jitter))
        msg["delay_s"] = round(delay, 4)
        self._queues[msg.get("receiver", "")].append(msg)

    def receive(self, role: str) -> list[dict]:
        msgs = self._queues.pop(role, [])
        self._received += len(msgs)
        return msgs

    def stats(self) -> dict:
        return {"sent": self._sent, "received": self._received,
                "dropped": self._dropped}


class SocketTransport(Transport):
    """占位：真多机演示彩蛋时实现（TCP/UDP 封装 V2X 消息）。"""

    def __init__(self, host: str = "127.0.0.1", port: int = 8900):
        self.host = host
        self.port = port

    def send(self, msg: dict) -> None:
        raise NotImplementedError("SocketTransport 用于真多机演示，当前未实现")

    def receive(self, role: str) -> list[dict]:
        raise NotImplementedError("SocketTransport 用于真多机演示，当前未实现")
