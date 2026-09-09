"""诊断：窗口化投放为何大部分车辆未进场。直接跑 sumo 捕获 stderr。"""
import os, sys, subprocess, tempfile, uuid, random
HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.dirname(HERE)
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)
from app.core.engine import Engine  # noqa: E402
from app.core.scenarios import _routes_from_net  # noqa: E402

NET = os.path.join(os.path.dirname(BACKEND), "networks", "network", "base_network.net.xml")
WINDOW = 150.0
PER_ROAD = (7, 12)

def write_file(routes, windowed):
    rng = random.Random()
    out = os.path.join(tempfile.gettempdir(), f"diag_{'win' if windowed else 'one'}_{uuid.uuid4().hex[:6]}.rou.xml")
    lo, hi = PER_ROAD
    lines = ['<?xml version="1.0" encoding="UTF-8"?>', "<routes>"]
    for i, edges in enumerate(routes):
        lines.append(f'    <route id="sc{i}" edges="{" ".join(edges)}"/>')
    vid = 0
    for i in range(len(routes)):
        for _ in range(rng.randint(lo, hi)):
            d = rng.uniform(0, WINDOW) if windowed else 0
            lines.append(f'    <vehicle id="scv{vid}" depart="{d:.1f}" departPos="random_free" route="sc{i}"/>')
            vid += 1
    lines.append("</routes>")
    open(out, "w", encoding="utf-8").write("\n".join(lines) + "\n")
    return out, vid

def run_with_stderr(route_file, tag):
    sumo = os.path.join(os.environ.get("SUMO_HOME", ""), "bin", "sumo.exe")
    if not os.path.exists(sumo):
        sumo = "sumo"
    cmd = [sumo, "-n", NET, "-r", route_file, "-b", "0", "-e", "200",
           "--step-length", "1", "--no-step-log", "--quit-on-end"]
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180)
    # 提取错误/告警行
    err_lines = [l for l in p.stderr.splitlines() if any(k in l for k in ("Error", "error", "not", "fail", "Warning", "unable", "insert"))]
    print(f"--- {tag}  exit={p.returncode}  stderr告警数={len(err_lines)}")
    for l in err_lines[:12]:
        print("   ", l.strip())
    return p

routes = _routes_from_net(NET)
print(f"路由数: {len(routes)}")
for windowed, tag in ((False, "one-shot"), (True, "windowed")):
    rf, nveh = write_file(routes, windowed)
    print(f"\n{tag}: 文件车辆定义数 = {nveh}")
    run_with_stderr(rf, tag)
