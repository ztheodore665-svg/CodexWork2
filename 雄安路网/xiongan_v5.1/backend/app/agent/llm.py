"""LLM 客户端：默认本地 Ollama（免费离线），内置智谱 GLM 免费 API 预设，可切换任意 OpenAI 兼容 API。

环境变量（显式设置优先于预设）：
    LLM_PROVIDER   ollama（默认，离线免费） | zhipu（智谱免费 API）
    LLM_BASE_URL   默认按 LLM_PROVIDER 取预设值
    LLM_MODEL      默认按 LLM_PROVIDER 取预设值
    LLM_API_KEY    默认按 LLM_PROVIDER 取预设值；智谱预设留空占位，填入真实密钥即可用

预设：
    ollama:  base_url=http://localhost:11434/v1  model=qwen2.5:3b  api_key=ollama
    zhipu:   base_url=https://open.bigmodel.cn/api/paas/v4  model=glm-4-flash  api_key=（留空，需用户填写）
"""

import os

try:
    from dotenv import load_dotenv  # pydantic-settings 自带依赖；失败则跳过
    _DOTENV_OK = True
except ImportError:  # pragma: no cover
    _DOTENV_OK = False

_dotenv_loaded = False


def _ensure_dotenv():
    """把 backend/.env 的 LLM_* 配置读入 os.environ（override=False，真实环境变量优先）。"""
    global _dotenv_loaded
    if _DOTENV_OK and not _dotenv_loaded:
        load_dotenv()
        _dotenv_loaded = True


# 智谱 API 密钥占位符（必须纯 ASCII，否则 HTTP 头编码报错；检测到未填写时给出明确提示）
ZHIPU_KEY_PLACEHOLDER = "sk-ZHISHU_API_KEY_NOT_CONFIGURED"

# 各 provider 可选模型（前端下拉展示，用户可运行时切换；默认取 preset.model）
MODEL_OPTIONS = {
    "zhipu": ["glm-4-flash", "glm-4.7-flash"],   # 均为免费模型
    "ollama": ["qwen2.5:3b", "qwen2.5:1.5b", "qwen3.5:9b"],
}

PROVIDERS = {
    "ollama": {
        "base_url": "http://localhost:11434/v1",
        "model": "qwen2.5:3b",
        "api_key": "ollama",
        "desc": "本地 Ollama（免费离线，答辩保底）",
    },
    "zhipu": {
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "model": "glm-4-flash",
        "api_key": "",
        "desc": "智谱 GLM 免费 API（需联网）",
    },
}


def resolve_config(model_override: str | None = None) -> dict:
    """解析当前 LLM 配置（预设 + 环境变量 + 运行时覆盖）。不创建客户端，供诊断/状态展示。"""
    _ensure_dotenv()
    provider = os.environ.get("LLM_PROVIDER", "ollama").strip().lower()
    preset = PROVIDERS.get(provider)
    if preset is None:
        preset = PROVIDERS["ollama"]
        provider = "ollama"
    base_url = os.environ.get("LLM_BASE_URL", preset["base_url"])
    model = model_override or os.environ.get("LLM_MODEL") or preset["model"]
    api_key = os.environ.get("LLM_API_KEY", preset["api_key"])
    key_configured = bool(api_key) and api_key not in (ZHIPU_KEY_PLACEHOLDER, "ollama")
    return {
        "provider": provider,
        "base_url": base_url,
        "model": model,
        "api_key_configured": key_configured,
        "preset_desc": preset["desc"],
        "model_options": MODEL_OPTIONS.get(provider, [preset["model"]]),
    }


def make_client(model_override: str | None = None):
    """返回 (OpenAI 兼容客户端, 模型名, 配置dict)。

    密钥未配置时仍返回客户端（用占位密钥），调用时 API 会返回 401，
    run_agent 的异常兜底会给出明确报错，不阻塞平台。
    """
    from openai import OpenAI
    cfg = resolve_config(model_override)
    api_key = os.environ.get("LLM_API_KEY", PROVIDERS[cfg["provider"]]["api_key"]) or ZHIPU_KEY_PLACEHOLDER
    if cfg["provider"] == "zhipu" and api_key == ZHIPU_KEY_PLACEHOLDER:
        print("[llm] 警告：LLM_PROVIDER=zhipu 但未设置 LLM_API_KEY（智谱密钥），"
              "对话将返回 401，请在 .env / 环境变量中填写密钥。")
    client = OpenAI(base_url=cfg["base_url"], api_key=api_key)
    return client, cfg["model"], cfg
