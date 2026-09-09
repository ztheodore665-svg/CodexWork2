from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """后端运行时配置，来自环境变量 / .env 文件。"""

    # SUMO 仿真引擎
    sumo_home: str = ""
    sim_config_path: str = ""
    sumo_comm_port: int = 8813

    # 网络服务
    backend_port: int = 8000
    frontend_port_dev: int = 5173
    frontend_port_prod: int = 80
    cache_addr: str = ""

    # 仿真默认参数
    step_length: float = 1.0
    begin_time: int = 0
    end_time: int = 86400

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
