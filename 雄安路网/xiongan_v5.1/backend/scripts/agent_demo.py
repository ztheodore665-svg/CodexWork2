"""LLM 协同管控智能体演示脚本。

流程：启动雄安仿真（方案二 MAPPO）→ 热机 → 注入突发车流 → 自然语言指令
驱动 LLM Agent（本地 Ollama，默认 qwen2.5:1.5b）读取指标、分析、调参、
生成报告，并对比注入前后指标。

用法：
    cd backend
    python scripts/agent_demo.py [--steps 热机步数] [--model qwen2.5:1.5b]
前置：已安装 Ollama 并 pull 模型（ollama pull qwen2.5:1.5b）。
"""

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.agent.agent import run_agent
from app.core.datacollector import DataCollector
from app.core.engine import Engine
from app.events.injector import EventInjector
from app.schemes.base import SchemeContext
from app.schemes.scheme2.controller import Scheme2Controller

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NET_DIR = os.path.join(os.path.dirname(BACKEND_DIR), "networks", "network")
DEFAULT_NET = os.path.join(NET_DIR, "base_network.net.xml")
DEFAULT_ROUTES = os.path.join(NET_DIR, "routes_clean700.rou.xml")
DEFAULT_ADD = os.path.join(NET_DIR, "timing_safe.xml")


class DemoRuntime:
    """把 Engine + 方案二控制器 + 采集器包装成 Agent 工具所需的 runtime 接口。"""

    def __init__(self, net, routes, add, weights):
        self.engine = Engine()
        self.engine.connect(net, [routes], [add], begin=0, end=20000,
                            step_length=1.0)
        self.collector = DataCollector(self.engine)
        ctx = SchemeContext(engine=self.engine, config={
            "torch_available": True, "mode": "auto",
            "mappo_weights": weights})
        self.scheme = Scheme2Controller(ctx)
        self.scheme.init()
        self.injector = EventInjector(SchemeContext(
            engine=self.engine, push_event=lambda *a, **k: None))
        self._step = 0
        self._events: list[dict] = []

    def step(self, n=1):
        for _ in range(n):
            self.engine.step()
            self.scheme.on_step()
            self._step += 1

    def status(self):
        return {"step": self._step}

    def realtime_metrics(self):
        return self.collector.snapshot()

    def list_events(self):
        return self._events

    def inject_event(self, event_type, params):
        result = self.injector.inject(event_type, params, self._step)
        self._events.append({"event_type": event_type, "params": params,
                             "result": result, "step": self._step})
        return result

    def close(self):
        self.scheme.cleanup()
        self.engine.close()


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--steps", type=int, default=600, help="热机步数")
    p.add_argument("--net", default=DEFAULT_NET)
    p.add_argument("--routes", default=DEFAULT_ROUTES)
    p.add_argument("--add", default=DEFAULT_ADD)
    p.add_argument("--weights", default="models/weights/mappo_act_full",
                   help="方案二 MAPPO 权重前缀")
    p.add_argument("--model", default=None,
                   help="LLM 模型（默认取环境变量 LLM_MODEL 或 qwen2.5:1.5b）")
    args = p.parse_args()
    if args.model:
        os.environ["LLM_MODEL"] = args.model

    rt = DemoRuntime(args.net, args.routes, args.add, args.weights)
    print(f"[demo] 仿真启动: mode={rt.scheme.mode} 热机 {args.steps} 步...")
    rt.step(args.steps)
    before = rt.realtime_metrics()["overall"]
    print(f"[demo] 注入前: veh={before['vehicle_count']} "
          f"speed={before['avg_speed']:.2f} wait={before['avg_waiting_time']:.0f}s")

    # 注入突发车流（大型活动）
    edges = ["E21_1", "E1_20", "E20_18", "E18_12", "E12_15", "E15_4", "E4_11"]
    rt.inject_event("large_event", {"edge_ids": edges, "vehicles": 60})
    print(f"[demo] 已注入突发车流 large_event（{len(edges)} 条边，60 辆）")
    rt.step(200)

    # 三个场景对话：①突发事件应急响应 ②交通咨询/驾驶建议 ③全局协调建议
    # 注：规则已要求"用户要求调控时必须实际执行"，故指令1 内即完成 分析+调参，无需第二条
    scenes = [
        ("场景一 · 突发事件应急响应", [
            "检测到 E21_1 附近突发车流，请分析现状并执行应急调控（调整信号配时或切换模式）。",
        ]),
        ("场景二 · 交通咨询/驾驶建议", [
            "我想从 E21_1 去 E9_19，请规划最优路径（调用 plan_route 工具）并给出驾驶建议。",
        ]),
        ("场景三 · 全局协调建议", [
            "请巡检全局路网，如发现拥堵区域请给出协调建议并执行。",
            "生成一份简要的态势报告。",
        ]),
    ]
    idx = 0
    for scene_title, msgs in scenes:
        print(f"\n########## {scene_title} ##########")
        for msg in msgs:
            idx += 1
            print(f"\n────── 指令 {idx}: {msg} ──────")
            reply, log = run_agent(rt, msg)
            for item in log:
                r = item["result"]
                print(f"  [工具] {item['tool']}({item['args']}) -> {str(r)[:160]}")
            print(f"  [Agent] {reply}")

    rt.step(300)
    after = rt.realtime_metrics()["overall"]
    print(f"\n[demo] 注入后(Agent 调控): veh={after['vehicle_count']} "
          f"speed={after['avg_speed']:.2f} wait={after['avg_waiting_time']:.0f}s")
    print(f"[demo] 对比: veh {before['vehicle_count']}→{after['vehicle_count']}  "
          f"speed {before['avg_speed']:.2f}→{after['avg_speed']:.2f}  "
          f"wait {before['avg_waiting_time']:.0f}→{after['avg_waiting_time']:.0f}s")
    rt.close()


if __name__ == "__main__":
    main()
