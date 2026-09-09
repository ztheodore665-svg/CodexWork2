"""V2X 消息中心：定义消息类型与云-边-车之间的收发。"""

from app.comms.transport import SimulatedTransport, Transport

MESSAGE_TYPES = {
    "BSV",           # 基本安全消息（车端广播位置/速度）
    "SPaT",          # 信号相位与配时（边缘→车端）
    "MAP",           # 地图/路口拓扑
    "ROUTE_GUIDE",   # 路线引导指令（云端→车端）
    "VEHICLE_STATUS",# 车辆状态上报（车端→云端）
    "EVENT_NOTICE",  # 事件通告（云端→边/车）
}


def build_message(mtype: str, sender: str, receiver: str,
                  payload: dict, sim_step: int) -> dict:
    return {"type": mtype, "sender": sender, "receiver": receiver,
            "payload": payload, "sim_step": sim_step}


class V2XHub:
    """云-边-车消息中转。各端 publish 到指定 receiver，另一端 poll 收取。"""

    def __init__(self, transport: Transport | None = None, **delay_params):
        self.transport = transport or SimulatedTransport(**delay_params)
        self._sent = 0
        self._received = 0

    def publish(self, mtype: str, sender: str, receiver: str,
                payload: dict, step: int) -> dict:
        if mtype not in MESSAGE_TYPES:
            raise ValueError(f"未知消息类型: {mtype}")
        msg = build_message(mtype, sender, receiver, payload, step)
        self.transport.send(msg)
        self._sent += 1
        return msg

    def poll(self, role: str) -> list[dict]:
        msgs = self.transport.receive(role)
        self._received += len(msgs)
        return msgs

    def stats(self) -> dict:
        return {"sent": self._sent, "received": self._received,
                "transport": self.transport.stats()}

    def reset(self) -> None:
        self._sent = 0
        self._received = 0
