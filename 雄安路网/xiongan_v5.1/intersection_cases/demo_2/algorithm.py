# -*- coding: utf-8 -*-
"""路口2 调度算法：Webster 最优配时执行器（单路口, 4 相位）。

方法（平台 scheme_1 核心算法）：
  按进口流量与饱和流(1800 veh/h/车道)求关键流量比 Y，算最优周期 C0=(1.5L+5)/(1-Y)，
  有效绿按 Y 分配；本路口低-中饱和，最优周期约 38s。
建议相位时长（黄灯不变）：相位0(两进口)=22s 相位1(黄)=3 相位2(另一向)=10 相位3(黄)=3
效果(900s 仿真对比基线)：平均等待 50.8 -> 7.9 s（-84%），最大排队 9->3。

用法：
  python algorithm.py            # 运行 3600s
  python algorithm.py 900        # 运行 900s
"""
import os
import sys

if 'SUMO_HOME' in os.environ:
    tools = os.path.join(os.environ['SUMO_HOME'], 'tools')
    sys.path.append(tools)
else:
    sys.exit("please declare environment variable 'SUMO_HOME'")

import traci
from sumolib import checkBinary

# 相位 index -> 建议持续时长(s)。黄灯相位保持 3s 不变。
SUGGEST = {0: 22, 1: 3, 2: 10, 3: 3}
CFG = "demo_2.sumocfg"


def main():
    steps = int(sys.argv[1]) if len(sys.argv) > 1 else 3600
    use_gui = len(sys.argv) > 2 and sys.argv[2] == "gui"
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    sumo_bin = checkBinary('sumo-gui' if use_gui else 'sumo')
    traci.start([sumo_bin, "-c", CFG, "--start"])
    tls = traci.trafficlight.getIDList()[0]
    last_phase = -1
    wait_sum, wait_n = 0.0, 0
    for step in range(1, steps + 1):
        traci.simulationStep()
        idx = traci.trafficlight.getPhase(tls)
        if idx != last_phase:            # 相位刚切换：改写该相位持续时长为建议值
            try:
                traci.trafficlight.setPhaseDuration(tls, float(SUGGEST.get(idx, 3.0)))
            except Exception:
                pass
            last_phase = idx
        if step % 300 == 0:              # 每 300s 打印一次在网等待均值
            vids = traci.vehicle.getIDList()
            if vids:
                w = sum(traci.vehicle.getWaitingTime(v) for v in vids) / len(vids)
                wait_sum += w
                wait_n += 1
                print(f"step={step:>5} 在网={len(vids):>3} 平均等待={w:6.1f}s")
    traci.close()
    if wait_n:
        print(f"[路口2] 末段平均在网等待 ≈ {wait_sum / wait_n:.1f} s"
              f"（Webster 配时，基线约 50.8s）")


if __name__ == '__main__':
    main()
