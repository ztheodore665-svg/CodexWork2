"""应用运行时：串起会话/采集/推送/指标存储/算法方案/路网缓存。"""

from app.core.arrival import ContinuousArrival
from app.core.datacollector import DataCollector
from app.core.engine import Engine
from app.core.safety_net import SafetyNet
from app.core.scenarios import (SCENARIOS, WARMUP_STEPS,
                                generate_flow_route_file, list_scenarios)
from app.core.session import Session, SessionError
from app.errors import SchemeError
from app.events.injector import EventInjector
from app.metrics.store import MetricsStore
from app.schemes import registry
from app.schemes.base import SchemeContext

VERSION = "1.0.0-xh202613"   # 技术指纹：版本号含发布方标识

# 内置路网中文名映射（仅前端展示用；name 保持英文供后端识别）
_NET_LABELS = {
    "base_network": "雄安容东 · 20 路口",
    "eval/eval_grid9": "随机网格 · 9 路口",
    "eval/eval_grid25": "随机网格 · 25 路口",
    "eval/eval_spider13": "蜘蛛网 · 13 路口",
    "demo_1": "案例路口1 · 十字+转向（6相位）",
    "demo_2": "案例路口2 · T形路口（4相位）",
    "demo_3": "案例路口3 · 十字路口（4相位）",
    "demo_4": "案例路口4 · 十字路口（4相位）",
}


def _net_label(name: str) -> str:
    if name in _NET_LABELS:
        return _NET_LABELS[name]
    if name.startswith("custom/"):
        return "自定义 · " + name.split("/", 1)[1]
    if name.startswith("train/"):
        return "训练路网 · " + name.split("/", 1)[1]
    return name


class AppRuntime:
    def __init__(self, ws_manager, settings, engine_factory=None):
        self.ws = ws_manager
        self.settings = settings
        self._engine_factory = engine_factory or Engine
        self.session: Session | None = None
        self.collector: DataCollector | None = None
        self.scheme = None
        self.safety_net: SafetyNet | None = None
        self.scenario: str = ""   # 当前交通场景 id（""=渐入·路网自带车流；normal=平峰等由后端密度生成）
        self.arrival: ContinuousArrival | None = None  # 峰期持续到达器（密度场景启用）
        self.store = MetricsStore()
        self.injector: EventInjector | None = None
        self._net_path = ""
        self._geojson: dict | None = None
        self._summary: dict | None = None
        self._net_paths: dict[str, str] = {}   # 路网名 → net 文件路径（list_networks 填充）
        self._preview_cache: dict[str, str] = {}  # 路网名 → 预览 SVG
        self.test_vehicle: dict | None = None  # 测试车辆跟踪状态
        self._agent_baseline: dict | None = None  # Agent 调控前后对比基线
        self._agent_conv: list[dict] = []      # Agent 会话记忆（跨轮）
        self._llm_model: str | None = None     # Agent 运行时模型覆盖（None=环境变量默认）

    # ── 仿真控制 ────────────────────────────────────────────

    def start(self, params: dict) -> dict:
        if self.session is not None:
            raise SessionError(1003, "仿真仍在运行，请先停止后再启动（或切换路网）")
        try:
            self._ensure_network(params.get("net_path"))
        except Exception as exc:  # 路网解析失败要如实报错，不能静默吞掉
            raise SessionError(1005, f"路网解析失败: {exc}") from exc
        # 交通场景：指定场景时按其密度生成一次性投放车流文件，替换默认 route 文件，
        # 并开启启动预热（无头高倍速投放+散开，期间前端不渲染，投放完再渲染）。
        # （复用路网自带路由；场景为启动参数，与"右转常绿/方案"正交）
        scenario = params.get("scenario") or ""
        if scenario and scenario in SCENARIOS:
            try:
                gen = generate_flow_route_file(
                    self._net_path, scenario,
                    (params.get("route_files") or [None])[0])
                params = {**params, "route_files": [gen],
                          "warmup": WARMUP_STEPS}
            except Exception:  # noqa: BLE001 生成失败回退渐入(路网自带车流)，不阻塞启动
                scenario = ""
        else:
            scenario = "" if scenario == "none" else scenario
        self.scenario = scenario or ""
        self.session = Session(engine_factory=self._engine_factory,
                               step_handler=self._on_step)
        try:
            sid = self.session.start(params)
            self.collector = DataCollector(self.session.engine)
            self.injector = EventInjector(SchemeContext(
                engine=self.session.engine, push_event=self._push_event))
            self.scheme = self._build_scheme(params.get("scheme", "none"),
                                             params.get("scheme_params") or {})
            if self.scheme is not None:
                self.scheme.init()
            # 峰期持续到达器：密度生成场景(scenario 在 SCENARIOS 中)启用，
            # 一次性投放+预热后仍按峰期强度持续随机补车；渐入/无场景不启用。
            if scenario in SCENARIOS:
                self.arrival = ContinuousArrival(self.session.engine, scenario)
            # 方案无关安全网（防溢出示绿 + 死锁清空），scheme 之后每步执行
            self.safety_net = SafetyNet(self.session.engine,
                                        params.get("safety_net") or {})
            return {"session_id": sid, "status": self.status()}
        except Exception:
            # 启动失败完整复位：关闭半开连接并清空状态，避免残留死会话
            # （"Connection 'default' is already active"）阻塞后续启动
            try:
                if self.session is not None and self.session.engine is not None:
                    self.session.engine.close()
            except Exception:  # noqa: BLE001 关闭失败不掩盖原始错误
                pass
            self.session = None
            self.collector = None
            self.injector = None
            self.scheme = None
            self.arrival = None
            self.scenario = ""
            self._net_path = ""
            raise

    def stop(self) -> dict:
        if self.session is None:
            raise SessionError(1001, "仿真未启动")
        if self.scheme is not None:
            self.scheme.cleanup()
        self.session.stop()
        # 完全复位：会话/采集/事件/路网缓存全部清空，避免残留导致
        # 切换路网不生效、新打开页面默认回到上次路网
        self.session = None
        self.collector = None
        self.scheme = None
        self.safety_net = None
        self.arrival = None
        self.scenario = ""
        self.injector = None
        self.test_vehicle = None
        self._geojson = None
        self._summary = None
        self._net_path = ""
        self.store = MetricsStore()
        return {"ok": True}

    def pause(self) -> dict:
        self._require_session().pause()
        return {"ok": True}

    def resume(self) -> dict:
        self._require_session().resume()
        return {"ok": True}

    def step_n(self, n: int) -> dict:
        self._require_session().step_n(n)
        return {"ok": True}

    def set_speed(self, speed: float) -> dict:
        self._require_session().set_speed(speed)
        return {"ok": True}

    def set_right_turn_green(self, enabled: bool) -> dict:
        """右转常绿开关（仿真运行中实时生效，不要求重启）。

        未启动仿真时安全返回（前端只在运行中调用）。
        """
        if self.session is None or self.session.engine is None:
            return {"ok": False, "applied": False, "reason": "no_sim"}
        self.session.engine.set_right_turn_green(bool(enabled))
        return {"ok": True, "applied": True, "enabled": bool(enabled)}

    def status(self) -> dict:
        if self.session is None:
            return {"state": "idle", "session_id": None, "step": 0,
                    "sim_time": 0, "scheme": "none", "scheme_mode": None,
                    "scenario": "",
                    "vehicle_count": 0}
        st = self.session.status()
        st["scenario"] = self.scenario
        st["safety_net"] = self.safety_net.status() if self.safety_net else None
        st["arrival"] = self.arrival.status() if self.arrival else None
        # 活动方案的控制器模式（如 scheme_2 的 mappo/scoot/auto），
        # 供前端顶栏实时展示 Agent / 用户运行中切换后的状态
        st["scheme_mode"] = None
        if self.scheme is not None:
            try:
                gs = self.scheme.handle_action("get_status", {})
                if isinstance(gs, dict):
                    st["scheme_mode"] = gs.get("mode") or None
            except Exception:  # noqa: BLE001
                st["scheme_mode"] = None
        return st

    def list_scenarios(self) -> list[dict]:
        """可选交通场景清单（前端右上角下拉）。"""
        return list_scenarios()

    # ── 每步回调（仿真线程） ─────────────────────────────────

    def _on_step(self, engine, step: int) -> None:
        if self.scheme is not None:
            self.scheme.on_step()
        if self.safety_net is not None:
            self.safety_net.on_step(step)
        if self.arrival is not None:
            self.arrival.decide(step)   # 峰期持续补车（在网软上限内）
        self._track_test_vehicle()
        data = self.collector.collect(step)
        # 每步轻量平均速度：既推给前端实时显示，也入历史供底部栏曲线
        #（overall 评价指标按 60 步粒度计算，若只靠它曲线会非常稀疏）
        avg_spd = self.collector.avg_speed()
        self.store.append(step, "avg_speed", avg_spd, "overall")
        self.ws.queue_put({"type": "simulation_step", "data": {
            "step": step,
            "simulation_time": engine.get_sim_time(),
            "vehicle_count": len(engine.get_vehicle_ids()),
            "avg_speed": avg_spd,
        }, "timestamp": step})
        self.ws.queue_put({"type": "vehicle_update",
                           "data": data["vehicles"], "timestamp": step})
        if data["tls"]:
            self.ws.queue_put({"type": "tls_update",
                               "data": data["tls"], "timestamp": step})
        if data["overall"] is not None:
            emissions = self.collector.emissions()
            self._record_metrics(step, data["overall"], data["intersections"],
                                 emissions)
            self.ws.queue_put({"type": "metrics_update",
                               "data": {"overall": data["overall"],
                                        "emissions": emissions},
                               "timestamp": step})

    def _record_metrics(self, step: int, overall: dict, intersections: dict,
                        emissions: dict | None = None) -> None:
        for metric in ("avg_delay", "throughput", "queue_length"):
            # 注：avg_speed 已由 _on_step 每步入历史，此处不重复记录
            if metric in overall:
                self.store.append(step, metric, overall[metric], "overall")
        if emissions:
            self.store.append(step, "fuel", emissions["fuel_consumed"], "overall")
            self.store.append(step, "co2", emissions["co2"], "overall")
        for tid, inter in intersections.items():
            for metric in ("queue_length", "throughput"):
                self.store.append(step, metric, inter.get(metric, 0), tid)

    # ── 方案 ────────────────────────────────────────────────

    def _build_scheme(self, name: str, params: dict):
        if name == "none" or not registry.has_scheme(name):
            return None
        cls = registry.get_scheme(name)
        ctx = SchemeContext(engine=self.session.engine, config=params,
                            push_event=self._push_event)
        return cls(ctx)

    def _push_event(self, event_type: str, message: str, details: dict | None = None):
        self.ws.queue_put({"type": "simulation_event", "data": {
            "event_type": event_type, "message": message, "details": details,
        }, "timestamp": self.status()["step"]})

    def list_schemes(self) -> list[dict]:
        return registry.list_schemes()

    def scheme_config(self, scheme_id: str, action: str, params: dict) -> dict:
        if not registry.has_scheme(scheme_id):
            raise SchemeError(3001, f"方案不存在: {scheme_id}")
        if self.scheme is not None and self.scheme.name == scheme_id:
            return self.scheme.handle_action(action, params)
        return {"ok": False, "message": "方案未激活，请先启动仿真并选中该方案"}

    def scheme_status(self, scheme_id: str) -> dict:
        if not registry.has_scheme(scheme_id):
            raise SchemeError(3001, f"方案不存在: {scheme_id}")
        if self.scheme is not None and self.scheme.name == scheme_id:
            return self.scheme.handle_action("get_status", {})
        return {"ok": False, "message": "方案未激活，请先启动仿真并选中该方案"}

    # ── 指标 ────────────────────────────────────────────────

    def realtime_metrics(self) -> dict:
        if self.collector is None:
            raise SessionError(1001, "仿真未启动")
        return self.collector.snapshot()

    def history_metrics(self, metric: str, start: int, end: int,
                        interval: int, scope: str = "overall") -> list[dict]:
        rows = self.store.query(metric, start, end, interval, scope)
        # 技术指纹（XH-202613）：avg_speed 序列中 step%97==0 的点叠加
        # +0.0007 微偏移（量级不可感知，仅用于版本溯源，不影响评估报告数据）。
        if metric == "avg_speed":
            rows = [{**r, "value": round(r["value"] + 0.0007, 4)}
                    if r["step"] % 97 == 0 else r for r in rows]
        return rows

    # ── 自定义指标（完成率 / 最久车辆 / 最堵道路 / 最堵路口） ──

    def spotlight(self) -> dict:
        """自定义指标聚合，供实时指标栏"自定义指标"卡片轮询。"""
        if self.session is None:
            raise SessionError(1001, "仿真未启动")
        eng = self.session.engine
        departed = eng.get_cumulative_departed()
        arrived = eng.get_cumulative_arrivals()
        out: dict = {
            "completion_rate": round(arrived / departed, 4) if departed else 0.0,
            "total_departed": departed,
            "total_arrived": arrived,
        }
        lv = eng.get_longest_travel_vehicle()
        if lv:
            out["longest_vehicle"] = lv
        lw = eng.get_longest_wait_vehicle()
        if lw:
            out["longest_wait_vehicle"] = lw
        mc = eng.get_most_congested_edge()
        if mc:
            out["most_congested_edge"] = mc
        tls = self._most_congested_tls()
        if tls:
            out["most_congested_tls"] = tls
        return out

    def _most_congested_tls(self) -> dict | None:
        """排队车辆最多的路口（复用采集器的路口指标）。"""
        if self.collector is None:
            return None
        try:
            inters = self.collector.snapshot().get("intersections", {})
        except Exception:  # noqa: BLE001
            return None
        best, best_id = -1, None
        for tid, v in inters.items():
            q = v.get("queue_length", 0)
            if q > best:
                best, best_id = q, tid
        if best_id is None:
            return None
        v = inters[best_id]
        return {"id": best_id, "queue": best,
                "waiting_time": round(v.get("waiting_time", 0.0), 1)}

    # ── 路网 ────────────────────────────────────────────────

    def network(self) -> dict:
        return self._geojson or {"type": "FeatureCollection", "features": []}

    def network_summary(self) -> dict:
        return self._summary or {}

    def network_geojson(self, net_path: str = "", name: str = "") -> dict:
        """按指定路网导出 GeoJSON（不启动仿真，供页面初始直接展示路网）。

        net_path 优先；name 走扫描缓存；解析失败返回空 FeatureCollection。
        """
        import os
        if name and not net_path:
            net_path = self._net_paths.get(name, "")
        if not net_path or not os.path.isfile(net_path):
            return {"type": "FeatureCollection", "features": []}
        try:
            from app.core import geojson as gj
            return gj.export_geojson(net_path)
        except Exception:  # noqa: BLE001
            return {"type": "FeatureCollection", "features": []}

    def list_networks(self) -> list[dict]:
        """扫描内置路网目录，返回可加载的路网清单（含配套车流/附加文件）。

        目录：<项目根>/networks/network/（雄安示例）与 <backend>/data/networks/**（训练/评估随机路网）。
        """
        import glob
        import os

        # runtime.py -> app/core -> app -> backend（需上三层）
        backend_dir = os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))))
        net_dirs = [
            os.path.join(os.path.dirname(backend_dir), "networks", "network"),
            os.path.join(backend_dir, "data", "networks"),
            # 主目录下的单路口案例集（demo_1..4，交付可见）
            os.path.join(os.path.dirname(backend_dir), "intersection_cases"),
        ]
        out: list[dict] = []
        seen: set[str] = set()
        for base in net_dirs:
            if not os.path.isdir(base):
                continue
            for net_file in sorted(glob.glob(os.path.join(base, "**", "*.net.xml"),
                                             recursive=True)):
                if net_file in seen:
                    continue
                seen.add(net_file)
                d = os.path.dirname(net_file)
                rel = os.path.relpath(d, base)
                stem = os.path.splitext(os.path.basename(net_file))[0]
                if stem.endswith(".net"):  # base_network.net.xml -> base_network
                    stem = stem[:-4]
                # 车流文件：同一目录下 *.rou.xml（排除 .rou.alt.xml 重复导出）
                routes = sorted(
                    f for f in glob.glob(os.path.join(d, "*.rou.xml"))
                    if not f.endswith(".rou.alt.xml"))
                # 附加文件：timing*.xml / *.add.xml
                adds = sorted(
                    set(glob.glob(os.path.join(d, "timing*.xml"))
                        + glob.glob(os.path.join(d, "*.add.xml"))))
                # 命名：顶层用文件名（base_network），子目录用目录路径（eval/eval_grid9、custom/my_net）
                name = rel if rel != "." else stem
                name = name.replace("\\", "/")
                out.append({
                    "name": name,
                    "label": _net_label(name),
                    "net_path": net_file,
                    "routes": routes,
                    "adds": adds,
                })
                self._net_paths[name] = net_file
        return out

    def network_preview_svg(self, name: str) -> str:
        """路网缩略预览 SVG（固定画布自适应，缓存）。"""
        import os
        if name in self._preview_cache:
            return self._preview_cache[name]
        if name not in self._net_paths:
            self.list_networks()  # 惰性填充路径缓存
        path = self._net_paths.get(name)
        if not path or not os.path.isfile(path):
            return ""
        try:
            import sumolib
            net = sumolib.net.readNet(path)
            xs, ys = [], []
            lines = []
            for edge in net.getEdges():
                shape = edge.getShape()
                if len(shape) < 2:
                    continue
                lines.append(shape)
                xs += [p[0] for p in shape]
                ys += [p[1] for p in shape]
            if not xs:
                return ""
            W, H = 360.0, 220.0
            pad = 14.0
            minx, maxx = min(xs), max(xs)
            miny, maxy = min(ys), max(ys)
            bw = max(maxx - minx, 1e-6)
            bh = max(maxy - miny, 1e-6)
            sc = min((W - 2 * pad) / bw, (H - 2 * pad) / bh)
            ox = (W - bw * sc) / 2 - minx * sc
            oy = (H - bh * sc) / 2 + maxy * sc  # Y 翻转烘焙进坐标（负缩放导致描边不渲染）
            parts = []
            for shape in lines:
                pts = " ".join(f"{ox + x * sc:.1f},{oy - y * sc:.1f}" for x, y in shape)
                parts.append(f'<polyline points="{pts}" fill="none" '
                             'stroke="#9aa5b1" stroke-width="1.4" />')
            polys = "".join(parts)
            svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W:.0f} {H:.0f}" '
                   f'style="background:#141517;border-radius:6px">'
                   f'<rect width="{W:.0f}" height="{H:.0f}" fill="#141517"/>'
                   f'<g stroke-linecap="round">{polys}</g></svg>')
            self._preview_cache[name] = svg
            return svg
        except Exception:  # noqa: BLE001 预览失败不影响功能
            return ""

    # ── 测试车辆（单车，可指定路线，走完后统计等待） ──────────

    def start_test_vehicle(self, from_edge: str, to_edge: str,
                           via: list[str] | None = None) -> dict:
        """按指定起终点（可途径边）加入一辆测试车，并开始跟踪。"""
        from app.errors import AppError
        if self.session is None:
            raise SessionError(1001, "仿真未启动")
        route = self._build_route(from_edge, to_edge, via or [])
        if not route:
            raise AppError(2002, f"无法从 {from_edge} 规划到 {to_edge} 的路线")
        step = self.session.status().get("step", 0)
        vid = f"test_{self.session.status().get('step', 0)}_{len(route)}"
        self.session.engine.add_vehicle_route(vid, route, depart=float(step))
        # 特殊标记：白色醒目
        try:
            import traci
            traci.vehicle.setColor(vid, (255, 255, 255, 255))
            traci.vehicle.setShape(vid, "passenger")
        except Exception:  # noqa: BLE001
            pass
        self.test_vehicle = {
            "vid": vid, "route": route, "step_added": step,
            "prev_wait": 0.0, "total_wait": 0.0, "max_wait": 0.0,
            "per_edge": {}, "arrived": False, "finished_at": None,
        }
        return {"vid": vid, "route": route}

    def _build_route(self, frm: str, to: str, via: list[str]) -> list[str] | None:
        """起终点 + 途径边 → 完整路线（分段 Dijkstra 拼接）。"""
        from app.agent.tools import _dijkstra
        eng = self.session.engine
        points = [frm] + via + [to]
        full: list[str] = []
        for a, b in zip(points, points[1:]):
            path, _ = _dijkstra(eng, a, b)
            if not path:
                return None
            if full:
                path = path[1:]
            full.extend(path)
        return full or None

    def test_vehicle_status(self) -> dict:
        if self.test_vehicle is None:
            return {"active": False}
        tv = self.test_vehicle
        return {
            "active": True,
            "vid": tv["vid"],
            "route": tv["route"],
            "arrived": tv["arrived"],
            "finished_at": tv["finished_at"],
            "total_wait": round(tv["total_wait"], 1),
            "max_wait": round(tv["max_wait"], 1),
            "per_edge": {k: round(v, 1) for k, v in tv["per_edge"].items()},
        }

    def _track_test_vehicle(self) -> None:
        """每步跟踪测试车辆等待时间；消失视为到达。"""
        tv = self.test_vehicle
        if tv is None or tv["arrived"]:
            return
        eng = self.session.engine
        try:
            state = eng.get_vehicle_state(tv["vid"])
        except Exception:  # noqa: BLE001 车辆不存在 → 已到达/离开
            tv["arrived"] = True
            tv["finished_at"] = self.status().get("sim_time", 0)
            return
        w = float(state.get("waiting_time", 0.0))
        delta = max(0.0, w - tv["prev_wait"])
        tv["prev_wait"] = w
        tv["total_wait"] += delta
        edge = state.get("lane", "")
        if edge:
            e = edge.rpartition("_")[0]
            tv["per_edge"][e] = tv["per_edge"].get(e, 0.0) + delta
            tv["max_wait"] = max(tv["per_edge"].values())  # 最差边累计等待

    def upload_network(self, files) -> dict:
        """保存上传的 SUMO 路网文件到 data/networks/custom/<net名>/，返回路网清单项。

        files: list[UploadFile]，按扩展名归类：.net.xml（路网，必需）/
        .rou.xml（车流）/ .add.xml 或 timing*.xml（附加）。
        """
        import os

        from app.errors import AppError

        backend_dir = os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))))
        net_up = None
        route_ups: list = []
        add_ups: list = []
        for f in files:
            name = os.path.basename(f.filename or "").lower()
            if not name:
                continue
            if name.endswith(".net.xml"):
                if net_up is not None:
                    raise AppError(2002, "只能上传一个路网文件（.net.xml）")
                net_up = f
            elif name.endswith(".rou.xml"):
                route_ups.append(f)
            elif name.endswith(".add.xml") or name.startswith("timing"):
                add_ups.append(f)
        if net_up is None:
            raise AppError(2002, "缺少路网文件（.net.xml）")

        stem = os.path.splitext(os.path.basename(net_up.filename or "net"))[0]
        if stem.endswith(".net"):
            stem = stem[:-4]
        target = os.path.join(backend_dir, "data", "networks", "custom", stem)
        os.makedirs(target, exist_ok=True)

        def save(f, prefix="") -> str:
            dst = os.path.join(target, (prefix or "") + os.path.basename(f.filename or "file"))
            with open(dst, "wb") as out:
                out.write(f.file.read())
            return dst

        net_path = save(net_up)
        routes = [save(r) for r in route_ups]
        adds = [save(a) for a in add_ups]
        return {
            "name": f"custom/{stem}",
            "net_path": net_path,
            "routes": routes,
            "adds": adds,
        }

    def _ensure_network(self, net_path: str | None) -> None:
        if not net_path or net_path == self._net_path:
            return
        try:
            from app.core import geojson as gj
            self._geojson = gj.export_geojson(net_path)
            self._summary = gj.network_summary(net_path)
            self._net_path = net_path
        except Exception:  # noqa: BLE001 路网加载失败不阻塞仿真启动
            self._geojson = None
            self._summary = None

    # ── 扰动事件注入 ────────────────────────────────────────

    def inject_event(self, event_type: str, params: dict) -> dict:
        if self.session is None or self.injector is None:
            raise SessionError(1001, "仿真未启动，无法注入事件")
        step = self.session.status()["step"]
        return self.injector.inject(event_type, params, step)

    def list_events(self) -> list[dict]:
        if self.injector is None:
            return []
        return self.injector.list_events()

    # ── 算法综合评分（老评价平台公式融合） ───────────────────

    def edge_stats(self, edge_id: str) -> dict:
        """单条边实时指标（点击道路详情用）。"""
        if self.session is None:
            raise SessionError(1001, "仿真未启动")
        eng = self.session.engine
        stats = eng.get_edge_stats(edge_id)
        tt = stats.get("travel_time", float("inf"))
        return {
            "edge_id": edge_id,
            "vehicle_count": stats.get("vehicle_count", 0),
            "mean_speed": round(stats.get("mean_speed", 0.0), 3),
            "occupancy": round(stats.get("occupancy", 0.0), 4),
            "travel_time": round(tt, 1) if tt != float("inf") else None,
            "speed_limit": round(eng.get_edge_speed_limit(edge_id), 2),
            "queue_length": eng.get_edge_queue(edge_id),
        }

    def vehicle_detail(self, veh_id: str) -> dict:
        """单辆车实时详情（含路线，点击车辆用）；附带节能/通行驾驶建议。"""
        if self.session is None:
            raise SessionError(1001, "仿真未启动")
        st = self.session.engine.get_vehicle_state(veh_id)
        try:
            st["advice"] = self.vehicle_advice(veh_id, st)
        except Exception:  # noqa: BLE001 建议为增强信息，失败不影响详情
            st["advice"] = None
        return st

    def vehicle_advice(self, veh_id: str, state: dict | None = None) -> dict:
        """车端驾驶建议（云/边下行指令的落地）：建议车速 + 前方路况提示。

        规则：按当前边限速与下游 1-2 条边的实时拥堵度给出节能/通行建议车速；
        属"规则 + 轻预测"的轻量化车端决策原型（无权重时 STGCN 亦按此兜底）。
        """
        if self.session is None:
            raise SessionError(1001, "仿真未启动")
        from app.core.datacollector import edge_from_lane
        eng = self.session.engine
        st = state or eng.get_vehicle_state(veh_id)
        edge = edge_from_lane(st.get("lane", ""))
        cur_kmh = round((st.get("speed", 0) or 0) * 3.6, 1)
        limit = 13.89
        try:
            limit = eng.get_edge_speed_limit(edge)
        except Exception:  # noqa: BLE001
            pass
        succ = []
        try:
            succ = list(eng.get_edge_successors(edge))[:2]
        except Exception:  # noqa: BLE001
            pass
        # 下游状态：最差一级决定整体状态与建议系数
        level_score = {"畅通": 0, "缓行": 1, "拥堵": 2}
        state_label, notes = "畅通", []
        for e in succ:
            try:
                st_e = eng.get_edge_stats(e)
                occ, ms = st_e.get("occupancy", 0) or 0, st_e.get("mean_speed", 0) or 0
                lv = "拥堵" if ms < 1.2 else ("缓行" if ms < 4.5 or occ > 0.35 else "畅通")
            except Exception:  # noqa: BLE001
                continue
            notes.append(f"{e}:{lv}")
            if level_score[lv] > level_score[state_label]:
                state_label = lv
        factor = {"畅通": 0.90, "缓行": 0.75, "拥堵": 0.55}[state_label]
        suggested = round(min(limit, limit * factor) * 3.6, 1)
        reason = f"当前限速 {round(limit * 3.6, 1)} km/h"
        if notes:
            reason += "；下游 " + "、".join(notes)
        reason += f"，建议按 {factor * 100:.0f}% 限速平稳行驶以节能省停"
        return {"current_kmh": cur_kmh, "suggested_kmh": suggested,
                "state": state_label, "reason": reason,
                "next_edges": succ, "suggested_mps": round(suggested / 3.6, 2)}

    def evaluate_score(self, overall: dict | None = None,
                       intersections: list | None = None) -> dict:
        """按《老评价平台 scoring.py》分层聚合公式评分。

        不传参时基于实时指标构造输入（完成率/等待为实时近似，
        精确值可在评估脚本中用 /evaluate/score POST 传入整体数据）。
        """
        from app.eval import scoring

        if self.session is None or self.collector is None:
            return {"passed": False, "reasons": ["仿真未启动"], "score": 0.0}

        if overall is None or intersections is None:
            live = self.realtime_metrics()
            ov = live.get("overall", {})
            ints = live.get("intersections", {})
            sim_time = max(1, self.status().get("sim_time", 0) or 1)
            throughput = ov.get("total_throughput", 0) or 0
            veh = ov.get("vehicle_count", 0) or 0
            departed = self.session.engine.get_cumulative_departed()
            overall = {
                # 完成率 = 到达/出发（标准定义；出发数在 metric 循环里已累计）
                "completion_rate": 100.0 * throughput / max(1, departed),
                "total_vehicle_passages": throughput,
                "simulation_duration": sim_time,
                "total_wait_time": (ov.get("avg_waiting_time", 0) or 0) * veh,
                "avg_cycle_time": 90.0,
            }
            intersections = [
                {"id": tid,
                 "vehicle_count": v.get("queue_length", 0) or 0,
                 "avg_wait_time": v.get("waiting_time", 0) or 0,
                 "cycle_time": 90.0}
                for tid, v in ints.items()]
        return scoring.compute_total_score(overall, intersections)

    # ── 健康检查 ────────────────────────────────────────────

    def health(self) -> dict:
        return {
            "service": "ok",
            "version": VERSION,
            "simulation": self.status(),
            "ws_clients": self.ws.client_count(),
        }

    # ── 内部 ────────────────────────────────────────────────

    def _require_session(self) -> Session:
        if self.session is None:
            raise SessionError(1001, "仿真未启动")
        return self.session
