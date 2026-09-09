import pytest

from app.core.session import Session, SessionError


def test_session_lifecycle(mock_factory):
    s = Session(engine_factory=mock_factory)
    sid = s.start({"net_path": "n", "begin": 0, "end": 100})
    assert s.status()["state"] == "running"
    assert sid
    s.pause()
    assert s.status()["state"] == "paused"
    s.resume()
    assert s.status()["state"] == "running"
    s.stop()
    assert s.status()["state"] == "idle"


def test_session_start_requires_net_path(mock_factory):
    s = Session(engine_factory=mock_factory)
    with pytest.raises(SessionError) as ei:
        s.start({})
    assert ei.value.code == 1004


def test_session_illegal_transition(mock_factory):
    s = Session(engine_factory=mock_factory)
    with pytest.raises(SessionError) as ei:
        s.pause()  # idle 不允许暂停
    assert ei.value.code == 1003


def test_session_step_n_paused_only(mock_factory):
    s = Session(engine_factory=mock_factory)
    s.start({"net_path": "n", "begin": 0, "end": 100})
    s.pause()
    s.step_n(3)
    assert s.status()["step"] >= 1
    s.stop()


def test_session_speed_zero_halts(mock_factory):
    s = Session(engine_factory=mock_factory)
    s.start({"net_path": "n", "begin": 0, "end": 100})
    s.set_speed(0)
    s.pause()  # speed=0 已挂起，暂停幂等路径
    s.resume()
    s.set_speed(1.0)
    s.stop()
    assert s.status()["state"] == "idle"
