"""feature flag 的三条验收（R3-1.5）：默认全关 / 环境变量可覆盖 / 唯一读取口。

为什么单独一份测试而不是挂在其他测试里：开关是「主流程是否会引入新分支」的总闸，
它一旦被误改成默认 True，`make test-degradation` 的"全关"前提就塌了，
而届时红的是一堆别的用例 —— 归因成本极高。这里把三条验收钉死在开关层。
"""

from __future__ import annotations

import pytest

from app.core.config import Settings, get_settings
from app.core.exceptions import ConfigError
from app.core.feature_flags import (
    FLAG_NAMES,
    STAGE3_FLAG_NAMES,
    STAGE6_FLAG_NAMES,
    STAGE7_FLAG_NAMES,
    all_flags,
    clean_settings,
    is_enabled,
    stage3_all_disabled,
    stage3_flags,
    stage6_all_disabled,
    stage6_flags,
)


def test_all_default_flags_are_disabled():
    """默认态（不看本机 .env）：全部开关一律 False —— PROJECT.md §二、[8]。"""
    settings = clean_settings()

    assert set(all_flags(settings).keys()) == set(FLAG_NAMES)
    for name in FLAG_NAMES:
        assert is_enabled(name, settings) is False, f"{name} 默认必须是 False"
    assert stage3_all_disabled(settings) is True
    assert stage6_all_disabled(settings) is True


def test_flag_names_cover_stage3_stage6_and_stage7():
    """三个分组不重不漏 —— 防止后来加开关时只加进一半。"""
    assert set(STAGE3_FLAG_NAMES) == {"tools", "cache", "rerank", "hnsw"}
    assert set(STAGE6_FLAG_NAMES) == {"photo_import", "privacy"}
    assert set(STAGE7_FLAG_NAMES) == {"semantic"}

    grouped = [STAGE3_FLAG_NAMES, STAGE6_FLAG_NAMES, STAGE7_FLAG_NAMES]
    union: set[str] = set()
    for group in grouped:
        assert len(set(group)) == len(group), "分组内部不许重复"
        union |= set(group)
    assert union == set(FLAG_NAMES)

    for i, left in enumerate(grouped):
        for right in grouped[i + 1 :]:
            assert set(left) & set(right) == set(), "分组之间不许重叠"


def test_stage6_flags_helper_only_returns_wired_flags():
    """阶段 6 的辅助函数只吐**实际接线**的开关 —— 多一个少一个都会让退化前提失真。

    阶段 16 之前这里断言「四个」，其中 `map_album` / `native_bridge` 从未被任何
    代码读取。把它们删掉不是减少覆盖，而是让这个断言描述真实的接线面。
    """
    settings = clean_settings()
    assert stage6_flags(settings) == dict.fromkeys(STAGE6_FLAG_NAMES, False)
    assert stage6_all_disabled(settings) is True


@pytest.mark.parametrize("name", STAGE3_FLAG_NAMES)
def test_env_overrides_each_stage3_flag(name: str, monkeypatch):
    """环境变量可覆盖。注意 `get_settings` 是 lru_cache 单例，改完 env 必须 cache_clear。"""
    monkeypatch.setenv(f"{name.upper()}_ENABLED", "true")
    get_settings.cache_clear()
    try:
        assert is_enabled(name) is True
        # 开关位一改，`all_flags` 必须立刻反映出来（排障时看的就是它）
        assert all_flags()[name] is True
    finally:
        get_settings.cache_clear()


@pytest.mark.parametrize("raw", ["1", "yes", "on", "true"])
def test_bool_parsing_accepts_common_truthy_values(raw: str, monkeypatch):
    """pydantic 的布尔解析：配置里写 `1` 也要生效，避免"明明开了却没开"。"""
    monkeypatch.setenv("TOOLS_ENABLED", raw)
    get_settings.cache_clear()
    try:
        assert is_enabled("tools") is True
    finally:
        get_settings.cache_clear()


def test_explicit_settings_argument_wins_over_env(monkeypatch):
    """显式传 `settings` 时不读全局单例 —— 单测才能独立于进程环境断言。"""
    monkeypatch.setenv("TOOLS_ENABLED", "true")
    get_settings.cache_clear()
    try:
        isolated = Settings(_env_file=None, tools_enabled=False)
        assert is_enabled("tools", isolated) is False
        assert is_enabled("tools") is True
    finally:
        get_settings.cache_clear()


def test_stage3_flags_helper_only_returns_four():
    settings = clean_settings()
    assert stage3_flags(settings) == dict.fromkeys(STAGE3_FLAG_NAMES, False)


def test_unknown_flag_raises_instead_of_silently_false():
    """打错的开关名必须当场报错：静默 False 会让功能"悄悄不生效"，排查成本极高。"""
    with pytest.raises(ConfigError) as excinfo:
        is_enabled("too1s")
    assert "too1s" in str(excinfo.value)
    assert "tools" in str(excinfo.value)  # 错误信息要给出已知清单，直接抄就能改对
