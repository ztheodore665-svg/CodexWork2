import json, time, urllib.request

BASE = "http://127.0.0.1:8021/api/v1"
NET = "C:/Users/27773/Desktop/xiongan_v5/networks/network"

def post(path, data):
    req = urllib.request.Request(BASE + path, data=json.dumps(data).encode("utf-8"),
                                 headers={"Content-Type": "application/json; charset=utf-8"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read().decode("utf-8"))

def get(path):
    with urllib.request.urlopen(BASE + path, timeout=60) as r:
        return json.loads(r.read().decode("utf-8"))

print("start:", post("/simulate/start", {
    "net_path": NET + "/base_network.net.xml",
    "route_files": [NET + "/routes_clean700.rou.xml"],
    "add_files": [NET + "/timing_safe.xml"],
    "scheme": "scheme_2", "end": 6000, "speed": 30,
    "scheme_params": {"mode": "auto", "mappo_weights": "models/weights/mappo_act_full"}}))
time.sleep(3)
print("status:", get("/simulate/status")["data"]["state"], get("/simulate/status")["data"]["scheme"])
