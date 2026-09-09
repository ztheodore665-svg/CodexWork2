import asyncio
import json

import pytest

from app.ws import protocol
from app.ws.manager import WSManager


def test_protocol_message_format():
    msg = json.loads(protocol.build_message("vehicle_update", {"a": 1}, 5))
    assert msg == {"type": "vehicle_update", "data": {"a": 1}, "timestamp": 5}


def test_protocol_rejects_bad_client_msg():
    with pytest.raises(protocol.ProtocolError) as ei:
        protocol.parse_client_message('{"type": "nope"}')
    assert ei.value.code == 4002
    with pytest.raises(protocol.ProtocolError) as ei2:
        protocol.parse_client_message("not-json")
    assert ei2.value.code == 4001


class FakeWS:
    def __init__(self):
        self.sent = []

    async def send_text(self, data):
        self.sent.append(data)


@pytest.mark.asyncio
async def test_manager_connect_and_drain():
    mgr = WSManager()
    ws = FakeWS()
    await mgr.connect(ws)
    assert json.loads(ws.sent[0])["type"] == "connected"
    assert mgr.client_count() == 1

    task = asyncio.create_task(mgr.drain())
    mgr.queue_put({"type": "simulation_step", "data": {"step": 1}, "timestamp": 1})
    for _ in range(100):
        await asyncio.sleep(0.01)
        if any(json.loads(m)["type"] == "simulation_step" for m in ws.sent):
            break
    assert any(json.loads(m)["type"] == "simulation_step" for m in ws.sent)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    await mgr.disconnect(ws)
