"""真实路网冒烟仿真：验证平台在真实 SUMO 上可运行。

用法：
    python scripts/smoke_real.py [步数] [方案] [注入事件]
    python scripts/smoke_real.py 300 scheme_1
    python scripts/smoke_real.py 600 scheme_2 large_event
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.datacollector import DataCollector
from app.core.engine import Engine

XIONGAN = "C:/Users/27773/Desktop/xiongan"
NET = os.path.join(XIONGAN, "network", "base_network.net.xml")
ROUTES = [os.path.join(XIONGAN, "network", "routes.rou.xml")]
ADD = [os.path.join(XIONGAN, "network", "timing.add.xml")]


def main() -> None:
    steps = int(sys.argv[1]) if len(sys.argv) > 1 else 300
    scheme = sys.argv[2] if len(sys.argv) > 2 else "none"
    event = sys.argv[3] if len(sys.argv) > 3 else None

    eng = Engine()
    eng.connect(NET, ROUTES, ADD, begin=0, end=max(100, steps), step_length=1.0)
    print(f"连接成功: edges={len(eng.get_edge_ids())} tls={len(eng.get_tls_ids())}")

    dc = DataCollector(eng)
    events = []
    from app.schemes.base import SchemeContext
    ctx = SchemeContext(engine=eng, push_event=lambda t, m, d=None: events.append((t, m)))
    ctrl = None
    if scheme != "none":
        from app.schemes import get_scheme
        ctrl = get_scheme(scheme)(ctx)
        ctrl.init()
        print(f"方案 {scheme} 已初始化")

    injector = None
    if event:
        from app.events.injector import EventInjector
        injector = EventInjector(ctx)
        print(f"注入事件: {event}")

    t0 = time.perf_counter()
    for step in range(1, steps + 1):
        eng.step()
        if ctrl:
            ctrl.on_step()
        if injector and step == steps // 2:
            if event == "large_event":
                injector.inject("large_event", {"edge_ids": eng.get_edge_ids()[:6], "vehicles": 30}, step)
            elif event == "construction":
                injector.inject("construction", {"edge_ids": eng.get_edge_ids()[:2]}, step)
        data = dc.collect(step)
        if step % 100 == 0:
            ov = data["overall"] if data["overall"] else dc.overall()
            print(f"step {step:4d}: vehicles={ov['vehicle_count']:3d} "
                  f"avg_speed={ov['avg_speed']:.1f} throughput={ov['total_throughput']}")

    overall = dc.overall()
    elapsed = time.perf_counter() - t0
    print("\n=== 结果 ===")
    print(f"仿真 {steps} 步, 耗时 {elapsed:.1f}s ({steps / elapsed:.0f} 步/秒)")
    print(f"全局指标: {overall}")
    print(f"排放: {dc.emissions()}")
    if ctrl:
        st = ctrl.handle_action("get_status", {})
        print(f"方案状态 keys: {sorted(st.keys())}")
    if injector:
        print(f"事件日志数: {len(injector.list_events())}, 事件推送数: {len(events)}")
    eng.close()
    print("冒烟通过")


if __name__ == "__main__":
    main()
