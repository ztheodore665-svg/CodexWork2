"""WebSocket 消息协议：统一消息格式 + 客户端消息解析校验。"""

import json
from typing import Any

# 服务端→客户端消息类型
SERVER_TYPES = {
    "connected", "simulation_step", "vehicle_update",
    "tls_update", "metrics_update", "simulation_event",
}
# 客户端→服务端消息类型
CLIENT_TYPES = {
    "set_speed", "pause", "resume", "stop", "step",
    "set_update_interval", "set_viewport", "subscribe",
}


class ProtocolError(Exception):
    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def build_message(mtype: str, data: dict, step: int) -> str:
    return json.dumps({"type": mtype, "data": data, "timestamp": step},
                      ensure_ascii=False)


def parse_client_message(raw: str) -> dict:
    """解析客户端消息并校验。非法格式抛 4001，未知类型抛 4002。"""
    try:
        msg = json.loads(raw)
    except (json.JSONDecodeError, TypeError) as exc:
        raise ProtocolError(4001, f"消息格式错误: {exc}") from exc
    if not isinstance(msg, dict) or "type" not in msg:
        raise ProtocolError(4001, "消息必须包含 type 字段")
    if msg["type"] not in CLIENT_TYPES:
        raise ProtocolError(4002, f"未知消息类型: {msg['type']}")
    return msg
