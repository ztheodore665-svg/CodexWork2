# -*- coding: utf-8 -*-
"""E2E: 启动仿真 -> agent 路径规划 -> 停止仿真（UTF-8 客户端，规避 PS 中文编码问题）"""
import json, time, urllib.request

BASE = "http://127.0.0.1:8021"
NET = "C:/Users/27773/Desktop/xiongan_v5/networks/network/base_network.net.xml"


def post(path, payload, timeout=180):
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(BASE + path, data=body,
                                 headers={"Content-Type": "application/json; charset=utf-8"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def get(path, timeout=30):
    with urllib.request.urlopen(BASE + path, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def main():
    # 1) 启动仿真
    start = post("/api/v1/simulate/start", {
        "net_path": NET,
        "config": None,
        "scheme": "scheme_2",
        "scheme_params": {"mode": "auto", "mappo_weights": "models/weights/mappo_act_full"}})
    print("start code:", start.get("code"))
    if start.get("code") != 0:
        print("start failed:", start)
        return 1
    sid = start["data"]["session_id"]
    print("session:", sid)
    time.sleep(2)

    # 2) agent 路径规划
    ans = post("/api/v1/agent/chat", {
        "message": "我想从 E21_1 去 E9_19，请规划最优路径并给出驾驶建议。"})
    data = ans.get("data") or {}
    print("reply:", data.get("reply", "")[:300])
    print("--- tool_calls ---")
    for tc in data.get("tool_calls") or []:
        print("tool:", tc["tool"], "args:", json.dumps(tc["args"], ensure_ascii=False))
        res = tc.get("result") or {}
        print("  ok:", res.get("ok"))
        if res.get("ok") and "route_edges" in res.get("data", {}):
            print("  route:", res["data"]["route_edges"])
            print("  travel_time_s:", res["data"].get("travel_time_s"))
        else:
            print("  msg:", res.get("message", ""))
    if not data.get("tool_calls"):
        print("FAIL: no tool calls")
        return 1
    route_ok = any(
        (tc.get("result") or {}).get("ok") and (tc.get("result") or {}).get("data", {}).get("route_edges")
        for tc in data.get("tool_calls") or [])
    print("ROUTE_OK:", route_ok)

    # 3) 停止仿真
    stop = post("/api/v1/simulate/stop", {})
    print("stop code:", stop.get("code"))
    return 0 if route_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
