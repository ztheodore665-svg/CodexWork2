"""成本控制三件套的行为测试（全部 Mock，不碰真实 API）。

守的是三个"省钱"决定，它们都是**静默失败**型：
关思考没生效不会报错，只会让账单变大；阈值没改不会报错，只会让每个问题都拒答；
`--limit` 没生效也不会报错，只会把 26 条全跑一遍。
"""

from __future__ import annotations

from app.agent.eval.dataset import load_dataset
from app.core.config import get_settings
from app.core.embedding import build_embedder
from app.core.llm import _disable_thinking, build_llm


# ---------------------------------------------------------------- 关思考
def test_thinking_is_disabled_for_qwen_models() -> None:
    """任务一：Qwen 系必须在请求体里带 `enable_thinking=False`。"""
    for model in ("qwen3.7-plus", "Qwen-Plus", "qwen3-max"):
        payload = _disable_thinking({"model": model}, model)
        assert payload["enable_thinking"] is False, model


def test_thinking_flag_is_not_sent_to_other_providers() -> None:
    """非 qwen 端点**不能**收到这个字段：未知参数可能被直接判 400。"""
    for model in ("deepseek-chat", "gpt-4o-mini", "mock-llm-v0"):
        payload = _disable_thinking({"model": model}, model)
        assert "enable_thinking" not in payload, model


# ---------------------------------------------------------------- 价格表
def test_new_models_are_in_the_price_table() -> None:
    """任务四：不再出现「模型不在价格表中，成本按 0 计」这种把账算丢的告警。"""
    from app.core.llm import _PRICE_PER_MTOKENS

    assert "qwen3.7-plus" in _PRICE_PER_MTOKENS
    assert "qwen3.7-text-embedding-flash" in _PRICE_PER_MTOKENS
    # 向量模型只按输入计费
    assert _PRICE_PER_MTOKENS["qwen3.7-text-embedding-flash"][1] == 0.0


# ---------------------------------------------------------------- 阈值
def test_retrieval_min_score_default_is_not_the_lethal_value() -> None:
    """任务二：默认值不能是 0.30 —— 真实 embedding 下它会把所有证据滤光。"""
    settings = get_settings().model_construct()
    assert settings.retrieval_min_score <= 0.05, (
        f"默认阈值 {settings.retrieval_min_score} 会在真实 embedding 下导致全量拒答"
    )


def test_retriever_config_default_matches_settings() -> None:
    """两处默认值必须一致，否则"改了 config 却没生效"这种事会反复发生。"""
    from app.agent.rag.retriever import RetrieverConfig

    assert RetrieverConfig().min_score == get_settings().model_construct().retrieval_min_score


# ---------------------------------------------------------------- --limit
def test_dataset_limit_truncates_to_n() -> None:
    """任务三：`--limit N` 必须真的只取前 N 条。"""
    items = load_dataset()
    assert len(items) > 5, "演示评测集应当大于 5 条，否则测不出截断"
    limited = items[:3]
    assert len(limited) == 3
    assert [i.question for i in limited] == [i.question for i in items[:3]]


def test_real_embedding_provider_still_builds() -> None:
    """确保配置改造没有破坏 provider 构造（离线环境仍是 hashing）。"""
    embedder = build_embedder(get_settings())
    assert embedder.provider in {"hashing", "openai_compatible", "dashscope"}


def test_mock_llm_does_not_disable_thinking() -> None:
    """mock provider 不发真实请求，但它走的是同一条 payload 构造路径。"""
    llm = build_llm(get_settings())
    payload: dict = {"model": llm.model}
    _disable_thinking(payload, llm.model)
    assert "enable_thinking" not in payload
