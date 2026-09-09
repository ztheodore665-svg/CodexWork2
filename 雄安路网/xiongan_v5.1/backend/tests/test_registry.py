import pytest

from app.schemes import get_scheme, has_scheme, list_schemes, register_scheme
from app.schemes.base import BaseScheme, SchemeContext


class DummyScheme(BaseScheme):
    """测试用方案。"""

    name = "scheme_dummy"

    def __init__(self, ctx):
        super().__init__(ctx)
        self.calls = 0

    def on_step(self):
        self.calls += 1


def test_register_and_get():
    register_scheme(DummyScheme)
    assert get_scheme("scheme_dummy") is DummyScheme
    assert has_scheme("scheme_dummy")
    assert any(s["id"] == "scheme_dummy" for s in list_schemes())


def test_get_missing_scheme_raises():
    with pytest.raises(KeyError):
        get_scheme("not_registered")


def test_scheme_lifecycle_hooks():
    register_scheme(DummyScheme)
    ctx = SchemeContext(engine=object())
    inst = get_scheme("scheme_dummy")(ctx)
    inst.init()
    inst.on_step()
    assert inst.calls == 1
    assert inst.handle_action("nope", {})["ok"] is False
    inst.cleanup()
