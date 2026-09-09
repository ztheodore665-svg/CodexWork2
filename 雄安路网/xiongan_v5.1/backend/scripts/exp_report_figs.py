# -*- coding: utf-8 -*-
"""实验评估报告图表生成：把历次实验数据固化为文档用图。

产出 docs/figs/*.png（对比柱状图 + 密度趋势 + 热点对比）。数据来源：
1. routes_clean700 窗口车流（评估用）：固定 38.5veh/3.07m/s/25s、
   SCOOT 63.5/1.71/60、MAPPO 49.0/1.77/45（贪心窗口均值）；
2. 峰期密度校准（scripts/exp_calibrate.py，同投放种子）：arrived(480s)；
3. 热点实验（scripts/exp_hotspot.py，同车流）：末段平均速度。
"""
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager

for p in (r"C:\Windows\Fonts\simhei.ttf",):
    if os.path.isfile(p):
        try:
            font_manager.fontManager.addfont(p)
            plt.rcParams["font.sans-serif"] = [font_manager.FontProperties(fname=p).get_name(), "DejaVu Sans"]
            break
        except Exception:
            continue
plt.rcParams["axes.unicode_minus"] = False

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "docs", "figs")   # backend/scripts → 项目根/docs/figs
os.makedirs(OUT, exist_ok=True)


def save(fig, name):
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, name), dpi=140, bbox_inches="tight")
    plt.close(fig)
    print("saved", name)


# ── 图1：routes_clean700 三方案横评（吞吐/速度/等待） ──
names = ["固定配时", "SCOOT", "MAPPO"]
veh = [38.5, 63.5, 49.0]
spd = [3.07, 1.71, 1.77]     # m/s
wait = [25, 60, 45]          # s
fig, axes = plt.subplots(1, 3, figsize=(10.5, 3.2))
colors = ["#495057", "#e8590c", "#2f9e44"]
axes[0].bar(names, veh, color=colors); axes[0].set_title("在网车辆（↑ 好）")
axes[1].bar(names, spd, color=colors); axes[1].set_title("平均速度 m/s（↑ 好）")
axes[2].bar(names, wait, color=colors); axes[2].set_title("平均等待 s（↓ 好）")
for ax in axes:
    ax.tick_params(labelsize=9); ax.grid(axis="y", linestyle="--", alpha=0.3)
save(fig, "fig1_schemes_benchmark.png")

# ── 图2：峰期密度校准 —— 自适应(SCOOT)相对固定配时的到达差（arrived 差） ──
bands = ["(1,2)", "(2,3)", "(3,5)", "(4,6)", "(5,8)", "(7,12)"]
arr_fixed = [2, 3, 8, 9, 19, 20]
arr_scoot = [4, 7, 13, 13, 11, 12]
fig, ax = plt.subplots(figsize=(8, 3.4))
x = range(len(bands))
ax.plot(x, arr_fixed, "-o", label="固定配时", color="#495057")
ax.plot(x, arr_scoot, "-s", label="SCOOT 自适应", color="#e8590c")
for i, (a, b) in enumerate(zip(arr_fixed, arr_scoot)):
    ax.annotate(f"{a}", (i, a), textcoords="offset points", xytext=(0, 6), fontsize=8, ha="center")
    ax.annotate(f"{b}", (i, b), textcoords="offset points", xytext=(0, -12), fontsize=8, ha="center", color="#e8590c")
ax.axvspan(2, 3.4, color="#2f9e44", alpha=0.08)   # 可区分窗口
ax.text(2.7, 21, "算法增益窗口 ≈ 300-400 投放", fontsize=9, color="#2f9e44", ha="center")
ax.set_xticks(list(x)); ax.set_xticklabels(bands)
ax.set_xlabel("投放密度（辆/路）"); ax.set_ylabel("480s 内到达数")
ax.set_title("峰期密度校准：均匀饱和后自适应无增益（>400 反劣化）")
ax.legend(); ax.grid(linestyle="--", alpha=0.3)
save(fig, "fig2_density_calibration.png")

# ── 图3：区域热点同车流 fixed vs scoot（末段平均速度） ──
fig, ax = plt.subplots(figsize=(7.6, 3.4))
steps = ["120", "180", "240", "300", "360", "420", "480"]
spd_fixed = [2.81, 2.75, 2.43, 2.35, 2.5, 2.4, 2.6]
spd_scoot = [2.81, 3.24, 3.62, 4.11, 3.9, 3.7, 3.64]
xs = list(range(len(steps)))
ax.plot(xs, spd_fixed, "-o", label="固定配时", color="#495057")
ax.plot(xs, spd_scoot, "-s", label="SCOOT 自适应", color="#e8590c")
ax.axvline(1.9, color="#adb5bd", linestyle=":", alpha=0.8)
ax.text(2.0, 3.0, "预热 90s 后接管控制", fontsize=8, color="#868e96")
ax.set_xticks(xs); ax.set_xticklabels(steps)
ax.set_xlabel("仿真时刻 s"); ax.set_ylabel("区域平均速度 m/s")
ax.set_title("区域热点场景：自适应疏通红区（后期均速高约 50%+）")
ax.legend(); ax.grid(linestyle="--", alpha=0.3)
save(fig, "fig3_hotspot_recover.png")

print("DONE ->", OUT)
