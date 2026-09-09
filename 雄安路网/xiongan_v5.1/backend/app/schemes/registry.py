"""算法插件注册中心：方案名 -> 实现类。"""

from app.schemes.base import BaseScheme

_REGISTRY: dict[str, type[BaseScheme]] = {}


def register_scheme(cls: type[BaseScheme]) -> type[BaseScheme]:
    if cls.name in _REGISTRY:
        print(f"[registry] 覆盖已注册方案: {cls.name}")
    _REGISTRY[cls.name] = cls
    return cls


def get_scheme(name: str) -> type[BaseScheme]:
    if name not in _REGISTRY:
        raise KeyError(f"方案未注册: {name}")
    return _REGISTRY[name]


def has_scheme(name: str) -> bool:
    return name in _REGISTRY


def list_schemes() -> list[dict]:
    return [
        {"id": name, "name": name,
         "description": (cls.__doc__ or "").strip().splitlines()[0] if cls.__doc__ else "",
         "available": True}
        for name, cls in _REGISTRY.items()
    ]
