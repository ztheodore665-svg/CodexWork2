import pytest

from app.comms.transport import SimulatedTransport
from app.comms.v2x import V2XHub


def test_v2x_message_roundtrip():
    hub = V2XHub(SimulatedTransport(base_delay=0, jitter=0, loss_rate=0))
    hub.publish("SPaT", "edge_T1", "cloud", {"state": "G"}, step=10)
    msgs = hub.poll("cloud")
    assert len(msgs) == 1
    assert msgs[0]["type"] == "SPaT"
    assert msgs[0]["sim_step"] == 10


def test_v2x_loss_drops_messages():
    hub = V2XHub(SimulatedTransport(base_delay=0, jitter=0, loss_rate=1.0))
    hub.publish("BSV", "veh_1", "edge_T1", {}, step=1)
    assert hub.poll("edge_T1") == []


def test_v2x_receiver_isolation():
    hub = V2XHub(SimulatedTransport(base_delay=0, jitter=0, loss_rate=0))
    hub.publish("VEHICLE_STATUS", "veh_1", "cloud", {"x": 1}, step=1)
    hub.publish("BSV", "veh_1", "edge_T1", {"y": 2}, step=1)
    assert len(hub.poll("cloud")) == 1
    assert len(hub.poll("edge_T1")) == 1


def test_v2x_unknown_type_raises():
    hub = V2XHub()
    with pytest.raises(ValueError):
        hub.publish("NOPE", "a", "b", {}, step=1)


def test_v2x_delay_recorded():
    hub = V2XHub(SimulatedTransport(base_delay=0.02, jitter=0, loss_rate=0))
    hub.publish("MAP", "cloud", "edge_T1", {}, step=1)
    msg = hub.poll("edge_T1")[0]
    assert msg["delay_s"] == pytest.approx(0.02)


def test_v2x_stats():
    hub = V2XHub(SimulatedTransport(base_delay=0, jitter=0, loss_rate=0.5))
    for _ in range(4):
        hub.publish("BSV", "v", "edge_T1", {}, step=1)
    hub.poll("edge_T1")
    stats = hub.stats()
    assert stats["sent"] == 4
    assert stats["received"] + stats["transport"]["dropped"] == 4
