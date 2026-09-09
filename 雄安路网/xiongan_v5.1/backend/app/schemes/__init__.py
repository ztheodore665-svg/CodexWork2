from app.schemes.registry import get_scheme, has_scheme, list_schemes, register_scheme
from app.schemes.scheme1.controller import Scheme1Controller  # noqa: F401 触发注册
from app.schemes.scheme2.controller import Scheme2Controller  # noqa: F401 触发注册
from app.schemes.scheme3.controller import Scheme3Controller  # noqa: F401 触发注册
from app.schemes.webster import WebsterController  # noqa: F401 触发注册（案例默认方案）
from app.schemes.official import OfficialPlansController  # noqa: F401 触发注册（官方方案·20路口路网）

__all__ = ["get_scheme", "has_scheme", "list_schemes", "register_scheme"]
