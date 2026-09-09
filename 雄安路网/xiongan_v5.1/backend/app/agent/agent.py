"""车路云协同管控 LLM 智能体（Traffic Copilot）。

两级架构：LLM（高层协调）负责"看态势 → 决定调哪个工具"，
MAPPO/SCOOT（低层执行）负责路口级实时控制——工具全部映射到平台已有能力。

- LLM 默认本地 Ollama（免费离线）；环境变量可切到任意 OpenAI 兼容 API。
- 工具调用采用**文本 JSON 协议**（兼容小模型与旧版 Ollama，不依赖 function calling）：
  LLM 输出 `{"tool": "...", "args": {...}}` 由本模块解析执行，工具结果回填后继续。
- 安全护栏在 tools.execute_tool 内（工具白名单 + 参数范围校验）。
"""

import json
import re

from app.agent.llm import make_client, resolve_config
from app.agent.tools import TOOL_SCHEMAS, execute_tool

TOOL_NAMES = [t["function"]["name"] for t in TOOL_SCHEMAS]


def _tool_docs() -> str:
    """由 TOOL_SCHEMAS 自动生成紧凑工具参数文档，供小模型参考。"""
    lines = []
    for t in TOOL_SCHEMAS:
        props = t["function"]["parameters"].get("properties", {})
        params = ", ".join(props.keys()) if props else "无参数"
        lines.append(f"- {t['function']['name']}({params})")
    return "\n".join(lines)


SYSTEM_PROMPT = f"""你是车路云协同管控智能体，运行在雄安车路云仿真平台上。可用工具及参数：
{_tool_docs()}

规则：
1. 需要数据或执行操作时，只输出一个 JSON（不要输出任何其他文字）：
   {{"tool": "工具名", "args": {{"参数": 值}}}}
2. 不需要工具时，直接输出中文回复。
3. 参数要在合理范围内（工具会校验）。
4. 突发车流/事故时先分析受影响区域，再执行调控。
5. 用户要求"调整/执行/切换/调控"等操作时，**必须调用对应工具实际执行**
   （configure_algorithm / algorithm_action / inject_event 等），执行完再总结结果；不要只给建议不执行。
6. **速度一律以 km/h 汇报**：工具返回的速度类指标（avg_speed 等）单位为 m/s，向用户汇报时必须
   换算成 km/h（数值 ×3.6）并标注单位 km/h，与平台主界面单位保持一致。

路网规划方法论（复杂任务按此展开，可多步调用）：
1. 观察：用 get_network_status / get_region_status / get_network_topology 了解全局与结构；
2. 定位：用 get_edge_status / get_tls_status / get_region_status 定位瓶颈边/路口/区域；
3. 行动：用 configure_algorithm / algorithm_action 调整算法参数或触发动作
   （如 switch_mode 切 MAPPO/SCOOT、enable_green_wave 开绿波、reroute_fleet 重路由）；
4. 验证：调控前先 compare_metrics 传 {{"action": "set"}} 记录基线，
   调控后 compare_metrics 传 {{"action": "compare"}} 对比前后指标并小结。

示例：
用户：路况如何？
助手：{{"tool": "get_network_status", "args": {{}}}}
用户：工具返回：{{"ok": true, "data": {{"vehicle_count": 45, "avg_speed": 0.89}}}}
助手：当前在网 45 辆，平均速度 3.2 km/h，路况总体正常。

用户：东侧拥堵，请调整最长绿灯
助手：{{"tool": "compare_metrics", "args": {{"action": "set"}}}}
用户：工具返回：{{"ok": true, "message": "已记录当前指标为基线", ...}}
助手：{{"tool": "configure_algorithm", "args": {{"algorithm_id": "scheme_2", "params": {{"max_green": 40}}}}}}
用户：工具返回：{{"ok": true, "data": {{"applied": ["max_green"], "params": {{"max_green": 40.0}}}}}}
助手：{{"tool": "compare_metrics", "args": {{"action": "compare"}}}}
用户：工具返回：{{"ok": true, "data": {{"comparison": {{"avg_speed": {{"before": 1.1, "after": 1.6, "delta": 0.5}}}}}}}}
助手：已将最长绿灯调至 40 秒，平均速度由 4.0 提升到 5.8 km/h，拥堵缓解。
"""

MAX_STEPS = 8


def _parse_action(text: str):
    """从回复文本解析工具动作 {"tool": ..., "args": {...}}；解析不到返回 None。

    对每个 '{' 做括号配对后用 json.loads 校验，兼容嵌套 args 与代码块包裹。
    """
    if not text:
        return None
    for m in re.finditer(r"\{", text):
        depth = 0
        for j in range(m.start(), len(text)):
            c = text[j]
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    try:
                        obj = json.loads(text[m.start():j + 1])
                    except json.JSONDecodeError:
                        break
                    if (isinstance(obj, dict) and "tool" in obj
                            and isinstance(obj.get("args"), dict)):
                        return str(obj["tool"]), obj["args"]
                    break
    return None


def run_agent(runtime, user_input: str, max_steps: int = MAX_STEPS):
    """执行一次 Agent 对话（携带跨轮会话记忆）。返回 (最终回复, 工具调用日志)。"""
    model_override = getattr(runtime, "_llm_model", None)
    client, model, _cfg = make_client(model_override)

    def _create(messages):
        """兼容混合思考模型：glm-4.7 等禁用思考，直接返回 content。"""
        kwargs = {"model": model, "messages": messages,
                  "temperature": 0.2, "max_tokens": 256}
        if model.startswith("glm-4.7"):
            kwargs["extra_body"] = {"thinking": {"type": "disabled"}}
        return client.chat.completions.create(**kwargs)

    conv = getattr(runtime, "_agent_conv", None)
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    if conv:
        messages.extend(conv[-40:])
    base_len = len(messages)
    messages.append({"role": "user", "content": user_input})
    log = []
    last_action = None
    repeat_count = 0
    try:
        for _ in range(max_steps):
            resp = _create(messages)
            text = (resp.choices[0].message.content or "").strip()
            action = _parse_action(text)
            if action is None:
                return text or "（模型未返回内容）", log
            # 连续重复同一动作视为无进展，强制终止并要求总结
            if action == last_action:
                repeat_count += 1
            else:
                last_action, repeat_count = action, 0
            if repeat_count >= 2:
                messages.append({"role": "user",
                                 "content": "你已重复调用同一工具且无新信息，请直接输出最终中文总结，"
                                            "不要再调用工具。"})
                continue
            name, args = action
            if not isinstance(args, dict):
                args = {}
            if name not in TOOL_NAMES:
                # 模型幻觉出不存在的工具：回填纠正，让模型重新选择
                messages.append({"role": "assistant", "content": text})
                messages.append({"role": "user",
                                 "content": f"工具 {name} 不存在，可用工具：{TOOL_NAMES}。"
                                            f"请重新输出一个有效的工具 JSON。"})
                continue
            result = execute_tool(runtime, name, args)
            log.append({"tool": name, "args": args, "result": result})
            messages.append({"role": "assistant", "content": text})
            messages.append({
                "role": "user",
                "content": f"工具 {name} 返回: {json.dumps(result, ensure_ascii=False)[:1200]}。"
                           f"如果任务已完成，请直接输出最终中文回复；否则只输出下一步工具 JSON。"})
        # 达到步数上限：强制基于已有工具结果给出最终总结
        messages.append({"role": "user",
                         "content": "工具调用已达上限，请基于以上所有工具结果，用中文给出最终总结回复，"
                                    "不要再输出 JSON。"})
        resp = _create(messages)
        final_text = (resp.choices[0].message.content or "（模型未返回内容）").strip()
        return final_text, log
    except Exception as exc:  # noqa: BLE001 LLM 不可用时不阻塞平台
        return f"[agent] LLM 调用失败: {type(exc).__name__}: {exc}", log
    finally:
        # 会话记忆：把本轮新产生的消息（含工具回填）追加进 runtime 记忆，截断保留最近 80 条
        if conv is not None:
            new_turns = messages[base_len:]
            if new_turns:
                conv.extend(new_turns)
                del conv[:-80]


def agent_status(model_override: str | None = None) -> dict:
    """返回 Agent 配置状态（模型/工具清单/LLM 后端）。"""
    cfg = resolve_config(model_override)
    return {
        "provider": cfg["provider"],
        "model": cfg["model"],
        "base_url": cfg["base_url"],
        "api_key_configured": cfg["api_key_configured"],
        "preset_desc": cfg["preset_desc"],
        "available_models": cfg["model_options"],
        "tools": TOOL_NAMES,
        "max_steps": MAX_STEPS,
        "protocol": "text-json",
    }
