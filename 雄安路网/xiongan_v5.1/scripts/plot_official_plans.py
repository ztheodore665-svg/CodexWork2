"""官方三档配时对比柱状图（docs/figs/exp_official_plans_{route}.png）。"""
import csv
import os
import sys
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm

# 中文字体
for f in (r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\simhei.ttf"):
    if os.path.isfile(f):
        fm.fontManager.addfont(f)
        plt.rcParams["font.family"] = fm.FontProperties(fname=f).get_name()
        break
plt.rcParams["axes.unicode_minus"] = False

route = sys.argv[1] if len(sys.argv) > 1 else "routes_clean700"
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
csv_path = os.path.join(BASE, "docs", "figs", f"exp_official_plans_{route}.csv")
rows = []
with open(csv_path, encoding="utf-8-sig") as f:
    for r in csv.DictReader(f):
        rows.append(r)

fig, ax = plt.subplots(1, 2, figsize=(13, 4.6))
names = [r["方案"] for r in rows]
waits = [float(r["平均等待s"]) for r in rows]
spds = [float(r["平均速度m_s"]) for r in rows]
x = range(len(names))
colors = ["#2f7ed8", "#2f7ed8", "#2f7ed8", "#f28f2b", "#9aa5b1"]
ax[0].bar(x, waits, color=colors)
ax[0].set_xticks(list(x)); ax[0].set_xticklabels(names, rotation=18, ha="right", fontsize=8)
ax[0].set_title("平均等待时间 (s，越低越好)")
ax[0].grid(axis="y", alpha=0.3)
for i, v in enumerate(waits):
    ax[0].text(i, v + 0.8, f"{v:.1f}", ha="center", fontsize=8)
ax[1].bar(x, spds, color=colors)
ax[1].set_xticks(list(x)); ax[1].set_xticklabels(names, rotation=18, ha="right", fontsize=8)
ax[1].set_title("平均速度 (m/s，越高越好)")
ax[1].grid(axis="y", alpha=0.3)
for i, v in enumerate(spds):
    ax[1].text(i, v + 0.03, f"{v:.2f}", ha="center", fontsize=8)
fig.suptitle(f"官方三档配时对比实验（base_network · {route} · 2400s）", fontsize=12)
fig.tight_layout()
out = os.path.join(BASE, "docs", "figs", f"exp_official_plans_{route}.png")
fig.savefig(out, dpi=160)
print("saved ->", out)
