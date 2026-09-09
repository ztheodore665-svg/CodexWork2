# -*- coding: utf-8 -*-
"""LLM API 连通性自检：打印当前配置解析结果，并尝试一次真实对话。

用法：
    python scripts/test_llm_api.py                     # 用当前环境变量（默认 ollama）
    set LLM_PROVIDER=zhipu && python scripts/test_llm_api.py   # 测智谱免费 API
    set LLM_API_KEY=你的密钥 && python scripts/test_llm_api.py # 填入密钥后复测
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.agent.llm import make_client, resolve_config  # noqa: E402


def main():
    cfg = resolve_config()
    print("=" * 60)
    print("LLM 配置解析结果")
    print(f"  provider            : {cfg['provider']}  ({cfg['preset_desc']})")
    print(f"  base_url            : {cfg['base_url']}")
    print(f"  model               : {cfg['model']}")
    print(f"  api_key_configured  : {cfg['api_key_configured']}")
    if cfg["provider"] == "zhipu" and not cfg["api_key_configured"]:
        print("  [!] 智谱密钥未配置：请在环境变量 LLM_API_KEY 填入 "
              "https://open.bigmodel.cn 的 API 密钥（形如 sk-...）")
    print("=" * 60)

    client, model, _ = make_client()
    print(f"尝试调用模型 {model} ...")
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": "回复：连接成功"}],
            temperature=0.0,
            max_tokens=16,
            timeout=30,
        )
        print(f"[OK] 调用成功：{resp.choices[0].message.content}")
        return 0
    except Exception as exc:  # noqa: BLE001
        msg = str(exc)
        if "401" in msg or "Authentication" in msg or "Invalid" in msg:
            print(f"[X] 认证失败（401）：请检查 LLM_API_KEY 是否正确。详情：{msg[:200]}")
        elif "404" in msg:
            print(f"[X] 模型不存在（404）：请检查 LLM_MODEL 是否正确。详情：{msg[:200]}")
        elif "Connection" in msg or "connect" in msg.lower() or "timed out" in msg.lower():
            print(f"[X] 网络不通：请检查网络 / base_url。详情：{msg[:200]}")
        else:
            print(f"[X] 调用失败：{msg[:300]}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
