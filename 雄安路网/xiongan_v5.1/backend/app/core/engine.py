"""TraCI 引擎适配器：封装 SUMO 读写接口，全部模块共用此契约。"""

import os

import traci


class EngineError(Exception):
    """引擎异常。code 对应 requirements 文档错误码：1001 未连接、1002 对象不存在。"""

    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class Engine:
    """对 SUMO/TraCI 的薄封装，方法签名即全项目共享契约。"""

    def __init__(self):
        self._connected = False
        self._step = 0
        self._net_file = ""
        self._route_files: list[str] = []
        self._graph: dict | None = None
        self._tls_phase_cache: dict[str, int] = {}
        self._tls_links_cache: dict[str, list] = {}
        self._cum_arrivals = 0
        self._cum_departed = 0
        self._edge_length_cache: dict[str, float] = {}
        self._edge_speed_cache: dict[str, float] = {}
        self._conn_dir_map: dict[str, dict[int, str]] = {}  # tls_id -> {linkIndex: dir}
        self._rt_original: dict[str, dict] = {}  # tls_id -> 右转常绿覆写前的原程序快照

    # ── 生命周期 ────────────────────────────────────────────

    def connect(self, net_file: str, route_files: list[str] | None = None,
                add_files: list[str] | None = None, begin: int = 0,
                end: int = 86400, step_length: float = 1.0) -> None:
        sumo_home = os.environ.get("SUMO_HOME", "")
        # 跨平台：Windows 用 <SUMO_HOME>/bin/sumo.exe；Linux/mac 直接用 PATH 中的 sumo
        if os.name == "nt":
            sumo_bin = os.path.join(sumo_home, "bin", "sumo.exe") if sumo_home else "sumo"
            if not os.path.exists(sumo_bin):
                sumo_bin = "sumo"
        else:
            sumo_bin = "sumo"
        cmd = [sumo_bin, "-n", net_file, "-b", str(begin), "-e", str(end),
               "--step-length", str(step_length), "--no-step-log", "--quit-on-end"]
        for rf in route_files or []:
            cmd += ["-r", rf]
        for af in add_files or []:
            cmd += ["-a", af]
        try:
            # ── TraCI 连接（跨机器兼容版）──────────────────────────
            # 部分 Windows 机器上 "localhost" 会优先解析到 ::1（sumo 只监听
            # IPv4），且连接未监听端口会挂起而非快速拒绝，导致 traci.start
            # 默认流程卡死。这里改为：手动指定空闲端口 + 显式 127.0.0.1 +
            # 先等 sumo 打开 TraCI 端口再连接。该写法在所有机器上均兼容。
            import socket as _socket
            import subprocess
            import time
            _s = _socket.socket()
            _s.bind(("", 0))
            port = _s.getsockname()[1]
            _s.close()
            proc = subprocess.Popen(
                cmd + ["--remote-port", str(port)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL)
            # sumo 的 TraCI 端口在加载路网前就会打开，等待 4s 足够；
            # 若 sumo 提前崩溃则立即报错，避免静默卡死。
            _start = time.time()
            while time.time() - _start < 4.0:
                if proc.poll() is not None:
                    raise RuntimeError(f"sumo 提前退出(code={proc.returncode})")
                time.sleep(0.2)
            traci.init(port, host="127.0.0.1", proc=proc, numRetries=10)
        except Exception as exc:  # noqa: BLE001 统一转成 EngineError 上报
            raise EngineError(1006, f"仿真启动失败: {exc}") from exc
        self._connected = True
        self._net_file = net_file
        self._route_files = route_files or []
        self._step = 0
        self._rt_original.clear()  # 新仿真进程，丢弃上个会话的右转原程序快照

    def close(self) -> None:
        if self._connected:
            try:
                traci.close()
            except Exception:  # noqa: BLE001 关闭失败不阻塞
                pass
        # 强制清理 traci 连接注册表：失败的 init 会在握手前就注册 'default'
        #（traci/connection.py __init__ 先 _connections[label]=self 再握手），
        # 而 traci.close() 只清理 ""（switch 后的别名）→ 残留 'default' 会
        # 导致下次 connect 报 "Connection 'default' is already active"。
        # 这里遍历注册表逐个关闭，彻底杜绝该残留。
        try:
            from traci import connection as _tcon
            for _lbl in list(_tcon._connections.keys()):
                _con = _tcon._connections.pop(_lbl, None)
                if _con is not None:
                    try:
                        _con.close()
                    except Exception:  # noqa: BLE001
                        pass
        except Exception:  # noqa: BLE001
            pass
        self._connected = False

    def step(self) -> int:
        self._require_connected()
        traci.simulationStep()
        self._step += 1
        return self._step

    def get_sim_time(self) -> float:
        self._require_connected()
        return float(traci.simulation.getTime())

    def net_file(self) -> str:
        return self._net_file

    # ── 读取类 ──────────────────────────────────────────────

    def get_edge_ids(self) -> list[str]:
        self._require_connected()
        return list(traci.edge.getIDList())

    def get_edge_stats(self, edge_id: str) -> dict:
        self._require_connected()
        try:
            return {
                "vehicle_count": int(traci.edge.getLastStepVehicleNumber(edge_id)),
                "mean_speed": float(traci.edge.getLastStepMeanSpeed(edge_id)),
                "occupancy": float(traci.edge.getLastStepOccupancy(edge_id)),
                "travel_time": float(traci.edge.getTraveltime(edge_id)),
            }
        except traci.TraCIException:
            return {"vehicle_count": 0, "mean_speed": 0.0, "occupancy": 0.0,
                    "travel_time": float("inf")}

    def get_edge_queue(self, edge_id: str) -> int:
        """该边当前车辆数（轻量，1 次 TraCI 调用）。"""
        self._require_connected()
        try:
            return int(traci.edge.getLastStepVehicleNumber(edge_id))
        except traci.TraCIException:
            return 0

    def get_vehicle_ids(self) -> list[str]:
        self._require_connected()
        return list(traci.vehicle.getIDList())

    def get_vehicle_state(self, veh_id: str) -> dict:
        self._require_connected()
        try:
            x, y = traci.vehicle.getPosition(veh_id)
            return {
                "x": float(x), "y": float(y),
                "angle": float(traci.vehicle.getAngle(veh_id)),
                "speed": float(traci.vehicle.getSpeed(veh_id)),
                "lane": traci.vehicle.getLaneID(veh_id),
                "type": traci.vehicle.getTypeID(veh_id),
                "route": list(traci.vehicle.getRoute(veh_id)),
                "waiting_time": float(traci.vehicle.getWaitingTime(veh_id)),
            }
        except traci.TraCIException as exc:
            raise EngineError(1002, f"车辆不存在: {veh_id}") from exc

    def get_departed_vehicles(self) -> list[str]:
        self._require_connected()
        return list(traci.simulation.getDepartedIDList())

    def get_arrived_vehicles(self) -> list[str]:
        self._require_connected()
        return list(traci.simulation.getArrivedIDList())

    def get_longest_travel_vehicle(self) -> dict | None:
        """在线行驶时间最长的车辆（当前时间 - depart 时间）。"""
        self._require_connected()
        now = float(traci.simulation.getTime())
        best, best_id = -1.0, None
        for vid in self.get_vehicle_ids():
            try:
                t = now - float(traci.vehicle.getDeparture(vid))
            except traci.TraCIException:
                continue
            if t > best:
                best, best_id = t, vid
        if best_id is None:
            return None
        st = self.get_vehicle_state(best_id)
        return {"id": best_id, "duration": round(best, 1),
                "waiting_time": round(st["waiting_time"], 1),
                "x": st["x"], "y": st["y"], "speed": round(st["speed"], 2)}

    def get_longest_wait_vehicle(self) -> dict | None:
        """累计等待时间最长的车辆。"""
        self._require_connected()
        best, best_id = -1.0, None
        for vid in self.get_vehicle_ids():
            try:
                w = float(traci.vehicle.getWaitingTime(vid))
            except traci.TraCIException:
                continue
            if w > best:
                best, best_id = w, vid
        if best_id is None:
            return None
        st = self.get_vehicle_state(best_id)
        return {"id": best_id, "waiting_time": round(best, 1),
                "x": st["x"], "y": st["y"], "speed": round(st["speed"], 2)}

    def get_most_congested_edge(self) -> dict | None:
        """排队车辆最多的道路（排除 SUMO 内部连接边 :xxx）。"""
        self._require_connected()
        best, best_id = -1, None
        for eid in self.get_edge_ids():
            if eid.startswith(":"):
                continue
            q = self.get_edge_queue(eid)
            if q > best:
                best, best_id = q, eid
        if best_id is None:
            return None
        st = self.get_edge_stats(best_id)
        return {"id": best_id, "queue": best,
                "mean_speed": round(st["mean_speed"], 2),
                "occupancy": round(st["occupancy"], 4)}

    def get_cumulative_arrivals(self) -> int:
        """累计到达车辆数（getArrivedNumber 是每步增量，需自行累加）。"""
        self._require_connected()
        self._cum_arrivals += int(traci.simulation.getArrivedNumber())
        return self._cum_arrivals

    def get_cumulative_departed(self) -> int:
        """累计出发车辆数（用于完成率 = 到达/出发）。"""
        self._require_connected()
        self._cum_departed += int(traci.simulation.getDepartedNumber())
        return self._cum_departed

    def get_edge_speed_limit(self, edge_id: str) -> float:
        self._require_connected()
        if edge_id in self._edge_speed_cache:
            return self._edge_speed_cache[edge_id]
        value = 13.89
        try:
            # TraCI 无 edge 级限速接口，取该边第 0 车道限速
            value = float(traci.lane.getMaxSpeed(f"{edge_id}_0")) or 13.89
        except traci.TraCIException:
            pass
        self._edge_speed_cache[edge_id] = value
        return value

    def get_edge_length(self, edge_id: str) -> float:
        self._require_connected()
        if edge_id in self._edge_length_cache:
            return self._edge_length_cache[edge_id]
        value = 0.0
        try:
            value = float(traci.lane.getLength(f"{edge_id}_0"))
        except traci.TraCIException:
            pass
        self._edge_length_cache[edge_id] = value
        return value

    def get_edge_successors(self, edge_id: str) -> list[str]:
        """边图中 edge_id 的后继边列表（从 net.xml 经 sumolib 解析并缓存）。"""
        return self._graph_cache().get(edge_id, [])

    def get_edge_road_type(self, edge_id: str) -> str:
        limit = self.get_edge_speed_limit(edge_id)
        if limit >= 16.7:
            return "arterial"
        if limit >= 11.1:
            return "secondary"
        return "local"

    def _graph_cache(self) -> dict:
        if self._graph is None:
            self._graph = {}
            try:
                import sumolib
                net = sumolib.net.readNet(self._net_file)
                self._graph = {
                    e.getID(): [s.getID() for s in e.getToNode().getOutgoing()]
                    for e in net.getEdges()
                }
            except Exception:  # noqa: BLE001
                self._graph = {}
        return self._graph

    def get_tls_position(self, tls_id: str):
        """信号灯坐标 (x, y) 或 None（TraCI 无法取得时）。"""
        self._require_connected()
        try:
            x, y = traci.junction.getPosition(tls_id)
            return float(x), float(y)
        except traci.TraCIException:
            return None

    def get_tls_ids(self) -> list[str]:
        self._require_connected()
        return list(traci.trafficlight.getIDList())

    def get_tls_state(self, tls_id: str) -> dict:
        self._require_connected()
        try:
            return {
                "state_str": traci.trafficlight.getRedYellowGreenState(tls_id),
                "phase_index": int(traci.trafficlight.getPhase(tls_id)),
                "num_phases": self._tls_phase_count(tls_id),
                "duration": float(traci.trafficlight.getPhaseDuration(tls_id)),
                "phase_duration": float(traci.trafficlight.getPhaseDuration(tls_id)),
                "elapsed": float(traci.trafficlight.getSpentDuration(tls_id)),
            }
        except traci.TraCIException as exc:
            raise EngineError(1002, f"信号灯不存在: {tls_id}") from exc

    def get_tls_connections(self, tls_id: str) -> dict:
        """相位索引 -> 受控车道列表（简化映射，供 Webster 相位-edge 反查）。"""
        self._require_connected()
        try:
            lanes = list(traci.trafficlight.getControlledLanes(tls_id))
            n = self._tls_phase_count(tls_id)
            return {i: lanes for i in range(n)}
        except traci.TraCIException as exc:
            raise EngineError(1002, f"信号灯不存在: {tls_id}") from exc

    def get_tls_links(self, tls_id: str) -> list[dict]:
        """受控 link 列表（与 state_str 字符一一对应）：[{from_edge, from_lane, dir}]。

        前端据此在对应进口方向绘制逐车道信号灯。静态数据，按 tls 缓存。
        dir 取自 net.xml 连接定义（s/l/r/t），供"隐藏右转灯"等按转向过滤。

        对齐关键：TraCI getControlledLinks / phase state 都按**全局 linkIndex**
        排序且**空槽占位**（部分路口 linkIndex 缺号，如 0,1,3,4… 缺 2 号，
        但 state 第 2 字符仍存在）。因此这里必须逐槽位遍历、空槽也保留占位项，
        并让返回列表与 state_str 同长同序——否则 links 压缩后序号与 state 字符
        错位，前端会把绿灯贴到错误进口（曾表现为“四直行同绿”等假冲突）。
        """
        cached = self._tls_links_cache.get(tls_id)
        if cached is not None:
            return cached
        self._load_conn_dir_map()
        links: list[dict] = []
        try:
            raw = traci.trafficlight.getControlledLinks(tls_id)
            for global_idx, lnk in enumerate(raw):
                inner = lnk[0] if (len(lnk) == 1 and isinstance(lnk[0], tuple)) else lnk
                parts = tuple(inner)
                frm = parts[0] if parts else ""
                lane_id = ""
                lane_idx = -1
                if isinstance(frm, (tuple, list)):
                    lane_id = str(frm[0]) if frm else ""
                    lane_idx = int(frm[1]) if len(frm) > 1 and isinstance(frm[1], int) else 0
                elif frm:
                    lane_id = str(frm)
                    lane_idx = 0
                if not lane_id:
                    # 空槽：SUMO 保留该 linkIndex 位置但无实际连接 → 占位保持序号对齐
                    links.append({"from_edge": "", "from_lane": -1,
                                  "dir": "", "_slot": global_idx})
                    continue
                # lane id 形如 "E14_1_0" → 边 "E14_1"，车道 0（从 id 尾部解析车道号）
                edge = lane_id.rpartition("_")[0]
                if lane_idx == 0 and "_" in lane_id:
                    tail = lane_id.rsplit("_", 1)[-1]
                    if tail.isdigit():
                        lane_idx = int(tail)
                if not edge:
                    links.append({"from_edge": "", "from_lane": -1,
                                  "dir": "", "_slot": global_idx})
                    continue
                # dir 按全局 linkIndex 槽位取（net.xml 连接 dir），与 state 字符对齐
                links.append({
                    "from_edge": edge,
                    "from_lane": lane_idx,
                    "dir": self._dir_at(tls_id, global_idx),
                    "_slot": global_idx,
                })
        except traci.TraCIException:  # noqa: BLE001
            pass
        self._tls_links_cache[tls_id] = links
        return links

    def get_tls_conn_details(self, tls_id: str) -> list[dict]:
        """受控 link 的详细连接信息（与 state_str 字符一一对应）。

        [{from_edge, from_lane, via_lane, to_edge, dir, _slot}]：via_lane 是路口内
        内部车道（若有），to_edge 是出口边（供防溢出示绿判堵）。
        防御式解析：getControlledLinks 在不同 SUMO 版本/路网下的元组顺序
        可能为 (from, via, to) 或 (from, to, via)，按":"内道前缀区分。
        与 get_tls_links 相同：空槽占位保留（_slot = 全局 linkIndex），
        返回列表与 state_str 同长同序。
        """
        cached = self._tls_links_cache.get(tls_id)
        if cached is not None and "via_lane" in (cached[0] if cached else {}):
            return cached
        self._load_conn_dir_map()
        links: list[dict] = []
        try:
            raw = traci.trafficlight.getControlledLinks(tls_id)
            for global_idx, lnk in enumerate(raw):
                inner = lnk[0] if (len(lnk) == 1 and isinstance(lnk[0], tuple)) else lnk
                parts = tuple(inner)
                frm = parts[0] if parts else ""
                lane_id = ""
                lane_idx = -1
                if isinstance(frm, (tuple, list)):
                    lane_id = str(frm[0]) if frm else ""
                    lane_idx = int(frm[1]) if len(frm) > 1 and isinstance(frm[1], int) else 0
                elif frm:
                    lane_id = str(frm)
                    lane_idx = 0
                if not lane_id:
                    links.append({"from_edge": "", "from_lane": -1,
                                  "via_lane": "", "to_edge": "",
                                  "dir": "", "_slot": global_idx})
                    continue
                edge = lane_id.rpartition("_")[0]
                if lane_idx == 0 and "_" in lane_id:
                    tail = lane_id.rsplit("_", 1)[-1]
                    if tail.isdigit():
                        lane_idx = int(tail)
                if not edge:
                    links.append({"from_edge": "", "from_lane": -1,
                                  "via_lane": "", "to_edge": "",
                                  "dir": "", "_slot": global_idx})
                    continue
                # 出口边/内部车道：parts[1..2] 防御式区分
                def _lane_name(x):
                    if isinstance(x, (tuple, list)) and x:
                        return str(x[0])
                    return str(x) if x else ""
                p1, p2 = _lane_name(parts[1] if len(parts) > 1 else ""), \
                         _lane_name(parts[2] if len(parts) > 2 else "")
                if p1.startswith(":"):
                    via, to_lane = p1, p2
                elif p2.startswith(":"):
                    via, to_lane = p2, p1
                else:
                    via, to_lane = "", p1 or p2
                # dir 按全局 linkIndex 槽位取（net.xml 连接 dir），与 state 字符对齐
                links.append({
                    "from_edge": edge,
                    "from_lane": lane_idx,
                    "via_lane": via,
                    "to_edge": to_lane.rpartition("_")[0] if to_lane else "",
                    "dir": self._dir_at(tls_id, global_idx),
                    "_slot": global_idx,
                })
        except traci.TraCIException:  # noqa: BLE001
            pass
        self._tls_links_cache[tls_id] = links
        return links

    def _load_conn_dir_map(self) -> None:
        """解析 net.xml 的连接定义：tls_id -> {linkIndex: dir}。

        <connection ... tl linkIndex dir/>：linkIndex 是该 tls 受控槽位的全局序号
        （部分路口存在空槽——net.xml 连接缺号但 SUMO phase state / TraCI
        getControlledLinks 仍为该序号保留位置，如某 tls 受控连接可能是 0,1,3,4...
        缺 2）。state_str 字符序 = 受控槽位按 linkIndex 升序，**空槽也占一个字符**，
        因此 dir 必须按 linkIndex 全局索引（缺失 idx 视为空槽，dir=''），
        不能压缩成连续列表，否则与 state 字符错位（曾导致“四直行同绿”等
        绿灯贴错方向的显示假象）。惰性加载 + 进程内缓存。
        """
        if self._conn_dir_map or not self._net_file:
            return
        import xml.etree.ElementTree as ET
        collected: dict[str, dict[int, str]] = {}
        try:
            root = ET.parse(self._net_file).getroot()
            for c in root.iter("connection"):
                tl = c.get("tl")
                idx = c.get("linkIndex")
                d = c.get("dir")
                if tl is None or idx is None or d is None:
                    continue
                collected.setdefault(tl, {})[int(idx)] = d
        except Exception:  # noqa: BLE001 解析失败则右转相关功能不可用
            self._conn_dir_map = {}
            return
        self._conn_dir_map = collected

    def _dir_at(self, tls_id: str, global_idx: int) -> str:
        """该 tls 全局第 global_idx 个受控槽位的转向（s/l/r/t），空槽/越界返回空串。

        global_idx 与 state_str 字符位置一一对应（含空槽占位）。
        """
        m = self._conn_dir_map.get(tls_id)
        if not m:
            return ""
        return m.get(global_idx, "")

    def right_turn_link_indices(self, tls_id: str) -> list[int]:
        """该信号机状态字中属于右转的字符下标（全局槽位序，与 state_str 对齐）。

        SUMO 的 dir 大小写都可能出现（'r'/'R'），统一视为右转。
        """
        self._load_conn_dir_map()
        m = self._conn_dir_map.get(tls_id, {})
        return sorted(i for i, d in m.items() if d in ("r", "R"))

    def turnaround_link_indices(self, tls_id: str) -> list[int]:
        """该信号机状态字中属于掉头(U-turn)的字符下标（全局槽位序，与 state_str 对齐）。

        dir='t'/'T'。官方配时普遍未给最左车道掉头设计相位 → 掉头车会饿死堵路，
        控制器(官方方案)把这类槽位在各绿灯相置小写 'g'(让行绿)做"掉头常绿"。
        """
        self._load_conn_dir_map()
        m = self._conn_dir_map.get(tls_id, {})
        return sorted(i for i, d in m.items() if d in ("t", "T"))

    def _tls_phase_count(self, tls_id: str) -> int:
        """当前信号方案的相位总数（缓存，避免高频调用昂贵定义接口）。"""
        cached = self._tls_phase_cache.get(tls_id)
        if cached is not None:
            return cached
        count = 1
        try:
            logics = traci.trafficlight.getCompleteRedYellowGreenDefinition(tls_id)
            if logics:
                active_id = traci.trafficlight.getProgram(tls_id)
                logic = next((lg for lg in logics if lg.programID == active_id),
                             logics[0])
                count = max(1, len(logic.phases))
        except (traci.TraCIException, AttributeError):
            pass
        self._tls_phase_cache[tls_id] = count
        return count

    # ── 写入类 ──────────────────────────────────────────────

    def set_tls_phase(self, tls_id: str, phase_index: int, duration: float) -> None:
        self._require_connected()
        try:
            traci.trafficlight.setPhase(tls_id, phase_index)
            traci.trafficlight.setPhaseDuration(tls_id, duration)
        except traci.TraCIException as exc:
            raise EngineError(1002, f"信号灯不存在: {tls_id}") from exc

    def set_tls_phase_duration(self, tls_id: str, duration: float) -> None:
        """改写当前相位剩余时长（不跳相位；Webster 配时逐相位生效用）。"""
        self._require_connected()
        try:
            traci.trafficlight.setPhaseDuration(tls_id, float(duration))
        except traci.TraCIException as exc:
            raise EngineError(1002, f"信号灯不存在: {tls_id}") from exc

    def set_tls_program(self, tls_id: str, program_id: str) -> None:
        self._require_connected()
        try:
            traci.trafficlight.setProgram(tls_id, program_id)
        except traci.TraCIException as exc:
            raise EngineError(1002, f"信号灯不存在: {tls_id}") from exc
        self._tls_phase_cache.pop(tls_id, None)

    def set_tls_phase_schedule(self, tls_id: str,
                               schedule: list[tuple[float, str]]) -> None:
        """整程序热替换：用给定相位序列 [(时长, state), ...] 替换该信号机当前程序。

        用于官方三档配时切换：每套官方配时展开成完整 SUMO 相位序列
        （绿/yellow/all_red 相位），在运行中整体换档（保留 programID）。
        state 串长度必须与该信号机受控连接数一致（linkIndex 升序）。
        """
        from traci._trafficlight import Logic
        from sumolib.net import Phase
        self._require_connected()
        if not schedule:
            return
        try:
            logics = traci.trafficlight.getCompleteRedYellowGreenDefinition(tls_id)
            if not logics:
                raise EngineError(1002, f"信号灯无程序: {tls_id}")
            # 必须以「当前激活程序」为基底替换（net.xml 与 add 文件可能各带一套
            # programID，取 logics[0] 可能替换到非激活程序而看不到效果）
            active_id = traci.trafficlight.getProgram(tls_id)
            logic = next((lg for lg in logics if lg.programID == active_id),
                         logics[0])
            phases = [Phase(float(dur), str(state)) for dur, state in schedule]
            new_logic = Logic(logic.programID, logic.type,
                              min(logic.currentPhaseIndex, len(phases) - 1),
                              phases, dict(logic.subParameter or {}))
            traci.trafficlight.setProgramLogic(tls_id, new_logic)
        except traci.TraCIException as exc:
            raise EngineError(1002, f"信号灯不存在: {tls_id}") from exc
        self._tls_phase_cache.pop(tls_id, None)
        self._tls_links_cache.pop(tls_id, None)

    def apply_right_turn_always_green(self) -> None:
        """右转常绿：运行时把每个信号机程序中右转 link 的状态字强制为 g（次要绿）。

        实现要点（兼容所有方案，含 legacy MAPPO）：
        - 用小写 'g'（让行绿/次要绿）而非大写 'G'：右转与直行共用车道时，
          SUMO 会告警 "Unsafe green phase ... targeted by 2 'G'-links"，且
          'g' 更符合真实工程（右转常绿但需让行直行/冲突车流）；
        - 仅"相位内已有其他绿"时把右转位置 g；全红清空相保持全红，不引入
          新的绿灯相 → 相位数量/结构不变，各方案观测与动作维度全部不受影响
          （green_phases 判断为 G 或 g，故 g 也不会改变绿相计数）；
        - 首次应用前记录原程序快照（_rt_original），供"关闭右转常绿"还原；
        - 用 setProgramLogic 整程序替换（保留原 programID 与相位时长）。
        """
        self._require_connected()
        for tid in self.get_tls_ids():
            right = self.right_turn_link_indices(tid)
            if not right:
                continue
            try:
                logics = traci.trafficlight.getCompleteRedYellowGreenDefinition(tid)
                active = traci.trafficlight.getProgram(tid)
                logic = next((lg for lg in logics if lg.programID == active),
                             logics[0])
                # 首次应用时记录原程序快照（重复应用不覆盖，保证可还原）
                if tid not in self._rt_original:
                    self._rt_original[tid] = self._snapshot_logic(logic)
                changed = False
                for ph in logic.phases:
                    st = list(ph.state)
                    # 该相位除右转外是否已有绿（全红清空相跳过，保持相位数不变）
                    has_green_other = any(
                        i not in right and ch in "Gg" for i, ch in enumerate(st))
                    if not has_green_other:
                        continue
                    for i in right:
                        if i < len(st):
                            st[i] = "g"
                            changed = True
                    ph.state = "".join(st)
                if changed:
                    traci.trafficlight.setProgramLogic(tid, logic)
                    self._tls_phase_cache.pop(tid, None)
            except Exception as exc:  # noqa: BLE001 单个路口失败不阻塞其余路口
                continue

    @staticmethod
    def _snapshot_logic(logic) -> dict:
        """把 traci Logic 对象转成可安全还原的快照（深拷贝相位数据）。"""
        return {
            "programID": logic.programID,
            "type": logic.type,
            "currentPhaseIndex": logic.currentPhaseIndex,
            "subParameter": dict(logic.subParameter or {}),
            "phases": [(ph.duration, ph.state, ph.minDur, ph.maxDur, ph.next, ph.name)
                       for ph in logic.phases],
        }

    def restore_right_turn_always_green(self) -> None:
        """关闭右转常绿：用记录的原程序快照还原所有被覆写的信号机。"""
        self._require_connected()
        for tid, snap in list(self._rt_original.items()):
            try:
                logic = self._build_logic_from_snapshot(snap)
                traci.trafficlight.setProgramLogic(tid, logic)
                self._tls_phase_cache.pop(tid, None)
            except Exception:  # noqa: BLE001 单个路口还原失败不阻塞其余路口
                continue
        self._rt_original.clear()

    def set_right_turn_green(self, enabled: bool) -> None:
        """右转常绿开关（可在仿真运行中实时调用）：enabled=True 应用，False 还原。"""
        if enabled:
            self.apply_right_turn_always_green()
        else:
            self.restore_right_turn_always_green()

    def _build_logic_from_snapshot(self, snap: dict):
        """从快照重建 traci Logic 对象（供还原 setProgramLogic）。"""
        from traci._trafficlight import Logic
        from sumolib.net import Phase
        phases = [Phase(d, s, mn, mx, nxt, nm)
                  for (d, s, mn, mx, nxt, nm) in snap["phases"]]
        return Logic(snap["programID"], snap["type"], snap["currentPhaseIndex"],
                     phases, snap.get("subParameter") or {})

    def get_vehicle_emissions(self, veh_id: str) -> dict:
        """车辆累计排放/油耗：fuel(L), co2/co/nox(g)。"""
        self._require_connected()
        try:
            return {
                "fuel": float(traci.vehicle.getFuelConsumption(veh_id)),
                "co2": float(traci.vehicle.getCO2Emission(veh_id)),
                "co": float(traci.vehicle.getCOEmission(veh_id)),
                "nox": float(traci.vehicle.getNOxEmission(veh_id)),
            }
        except traci.TraCIException as exc:
            raise EngineError(1002, f"车辆不存在: {veh_id}") from exc

    def set_vehicle_route(self, veh_id: str, edges: list[str]) -> None:
        self._require_connected()
        try:
            traci.vehicle.setRoute(veh_id, edges)
        except traci.TraCIException as exc:
            raise EngineError(1002, f"车辆不存在: {veh_id}") from exc

    def reroute_vehicle(self, veh_id: str, with_travel_time: bool = True) -> None:
        self._require_connected()
        try:
            traci.vehicle.rerouteTraveltime(veh_id)
        except traci.TraCIException as exc:
            raise EngineError(1002, f"车辆不存在: {veh_id}") from exc

    def set_edge_speed_limit(self, edge_id: str, speed: float) -> None:
        """运行时调整某条边所有车道的限速（用于施工/事故扰动）。"""
        self._require_connected()
        try:
            n = int(traci.edge.getLaneNumber(edge_id))
            for i in range(n):
                traci.lane.setMaxSpeed(f"{edge_id}_{i}", float(speed))
        except traci.TraCIException as exc:
            raise EngineError(1002, f"边不存在: {edge_id}") from exc
        self._edge_speed_cache[edge_id] = float(speed)

    def add_vehicle_route(self, veh_id: str, route_edges: list[str],
                          depart: float = 0.0, veh_type: str | None = None) -> None:
        """按完整边路线动态注入一辆车（支持任意类型）。"""
        self._require_connected()
        try:
            route_id = f"{veh_id}_route"
            traci.route.add(route_id, list(route_edges))
            vt = veh_type or "DEFAULT_VEHTYPE"
            if vt != "DEFAULT_VEHTYPE":
                try:
                    traci.vehicletype.copy("DEFAULT_VEHTYPE", vt)
                    if vt in ("bus", "truck"):
                        traci.vehicletype.setLength(vt, 10.0)
                        traci.vehicletype.setMaxSpeed(vt, 13.89)
                        traci.vehicletype.setColor(vt, (255, 107, 107, 255) if vt == "bus"
                                                   else (177, 151, 252, 255))
                    elif vt == "bicycle":
                        traci.vehicletype.setLength(vt, 2.0)
                        traci.vehicletype.setMaxSpeed(vt, 6.0)
                        traci.vehicletype.setColor(vt, (99, 230, 190, 255))
                    elif vt == "fleet":
                        traci.vehicletype.setColor(vt, (255, 169, 77, 255))
                except traci.TraCIException:
                    pass  # 类型已存在（重复注入时）
            traci.vehicle.add(veh_id, route_id, typeID=vt, depart=float(depart))
        except traci.TraCIException as exc:
            raise EngineError(1002, f"无法添加车辆 {veh_id}: {exc}") from exc

    def add_vehicle_trip(self, veh_id: str, from_edge: str, to_edge: str,
                         depart: float = 0.0, veh_type: str | None = None) -> None:
        """动态注入一辆两点车（用于大型活动突发车流/人工加车）。"""
        self.add_vehicle_route(veh_id, [from_edge, to_edge], depart, veh_type)

    # ── 内部 ────────────────────────────────────────────────

    def _require_connected(self) -> None:
        if not self._connected:
            raise EngineError(1001, "仿真引擎未连接")
