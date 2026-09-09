from app.config import Settings


def test_settings_defaults(monkeypatch):
    # 隔离外部环境变量，避免本机 SUMO_HOME 干扰默认值断言
    for key in ("SUMO_HOME", "SIM_CONFIG_PATH", "BACKEND_PORT", "SUMO_COMM_PORT"):
        monkeypatch.delenv(key, raising=False)
    s = Settings()
    assert s.backend_port == 8000
    assert s.sumo_comm_port == 8813
    assert s.sumo_home == ""
    assert s.step_length == 1.0
    assert s.end_time == 86400


def test_settings_override_from_env(monkeypatch):
    monkeypatch.setenv("BACKEND_PORT", "9000")
    monkeypatch.setenv("SUMO_HOME", "C:/sumo")
    s = Settings()
    assert s.backend_port == 9000
    assert s.sumo_home == "C:/sumo"
