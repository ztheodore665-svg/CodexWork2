"""算法适配器：把 BaseScheme 方案包装成标准化算法（MCP server 侧）。

职责：
  - schema()   暴露参数/观测/指标/能力声明
  - state()    返回当前参数值 + 框架统一观测快照 + 算法内部指标
  - config()   校验并应用参数（复用现有 handle_action("set_params")，未实现则仅存配置）
  - action()   通用动作透传（tools/call）
"""

from typing import Any

from app.algorithms.base import AlgorithmSpec, ParamSpec, OBS_GLOBAL, OBS_STRUCTURED


def _validate_params(specs: list[ParamSpec], params: dict) -> tuple[dict, str | None]:
    """按 ParamSpec 校验参数（类型/范围/枚举），返回 (合法参数, 错误信息)。"""
    out: dict[str, Any] = {}
    for p in specs:
        if p.key not in params:
            continue
        v = params[p.key]
        if p.type == "number":
            try:
                v = float(v)
            except (TypeError, ValueError):
                return {}, f"参数 {p.label}({p.key}) 需为数字"
            if p.minimum is not None and v < p.minimum:
                return {}, f"参数 {p.label} 低于下限 {p.minimum}"
            if p.maximum is not None and v > p.maximum:
                return {}, f"参数 {p.label} 超过上限 {p.maximum}"
        elif p.type == "enum":
            if p.enum:
                if isinstance(v, str):
                    # 容错：忽略大小写与首尾空格（LLM 常把 SCOOT/MAPPO 写成大写）
                    key = v.strip().lower()
                    hit = next((e for e in p.enum
                                if str(e).strip().lower() == key), None)
                    if hit is None:
                        return {}, f"参数 {p.label} 不在允许范围 {p.enum}"
                    v = hit
                elif v not in p.enum:
                    return {}, f"参数 {p.label} 不在允许范围 {p.enum}"
        elif p.type == "bool":
            v = bool(v)
        else:
            v = str(v)
        out[p.key] = v
    return out, None


class AlgorithmAdapter:
    """把一个已注册的方案包装成标准算法。scheme 为 None 表示未激活。"""

    def __init__(self, runtime, scheme_id: str, scheme=None):
        self.rt = runtime
        self.scheme_id = scheme_id
        self.scheme = scheme  # BaseScheme 实例或 None
        # 类级声明（未激活也可读，来自注册表）
        from app.schemes import registry
        self._cls = registry.get_scheme(scheme_id) if registry.has_scheme(scheme_id) else None

    @property
    def spec(self) -> AlgorithmSpec | None:
        if self._cls is None:
            return None
        return getattr(self._cls, "algorithm_spec", None)

    def schema(self) -> dict:
        """MCP tools/get：完整声明。"""
        base = {
            "id": self.scheme_id,
            "active": self.scheme is not None,
        }
        if self.spec is None:
            return {**base, "kind": "unknown", "description": "",
                    "params": [], "observables": [], "metrics": [],
                    "capabilities": []}
        sp = self.spec.to_dict()
        # 附带当前参数值
        current = {}
        if self.scheme is not None:
            current = self._current_params()
        return {**base, **sp, "current": current}

    def _current_params(self) -> dict:
        """从方案实例读当前参数值（handle_action("get_params") 优先，回退默认值）。"""
        if self.scheme is None:
            return {}
        try:
            res = self.scheme.handle_action("get_params", {})
            if isinstance(res, dict) and res.get("ok") and isinstance(res.get("params"), dict):
                return res["params"]
        except Exception:  # noqa: BLE001
            pass
        out = {}
        for p in (self.spec.params if self.spec else []):
            out[p.key] = p.default
        return out

    def state(self) -> dict:
        """MCP resources/read：当前参数 + 观测快照 + 内部指标。"""
        out = {"id": self.scheme_id, "active": self.scheme is not None}
        if self.scheme is None:
            out["reason"] = "方案未激活（请先启动仿真并选中该方案）"
            return out
        keys = list(self.spec.observables) if self.spec else []
        out["params"] = self._current_params()
        out["observations"] = self._collect_observations(keys)
        out["metrics"] = self._internal_metrics()
        return out

    def config(self, params: dict) -> dict:
        """MCP tools/call（参数部分）：校验 → 应用 → 返回新值。"""
        if self.scheme is None:
            return {"ok": False, "message": "方案未激活"}
        specs = self.spec.params if self.spec else []
        valid, err = _validate_params(specs, params or {})
        if err:
            return {"ok": False, "message": err}
        if not valid:
            return {"ok": True, "params": self._current_params(), "applied": []}
        # 优先走标准动作 set_params；未实现的方案仅存入 ctx.config（由算法 init 读取）
        res = None
        try:
            res = self.scheme.handle_action("set_params", dict(valid))
        except Exception:  # noqa: BLE001
            res = None
        applied = []
        if isinstance(res, dict) and res.get("ok"):
            applied = list(valid.keys())
        else:
            cfg = dict(self.scheme.ctx.config or {})
            cfg.update(valid)
            self.scheme.ctx.config = cfg
            applied = list(valid.keys())
        return {"ok": True, "applied": applied,
                "params": self._current_params(),
                "note": "已存入方案配置（部分算法需重启仿真或下次决策生效）"
                if not (isinstance(res, dict) and res.get("ok")) else None}

    def action(self, action: str, params: dict) -> dict:
        """MCP tools/call：通用动作透传。"""
        if self.scheme is None:
            return {"ok": False, "message": "方案未激活"}
        try:
            return self.scheme.handle_action(action, params or {})
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "message": str(exc)}

    def _internal_metrics(self) -> dict:
        if self.scheme is None:
            return {}
        fn = getattr(self.scheme, "get_internal_metrics", None)
        if callable(fn):
            try:
                m = fn()
                if isinstance(m, dict):
                    return m
            except Exception:  # noqa: BLE001
                pass
        return {}

    # ── 框架统一观测采集（口径固定） ────────────────────────

    def _collect_observations(self, keys: list[str]) -> dict:
        rt = self.rt
        out: dict[str, Any] = {}
        needed = set(keys)
        if not needed:
            return out

        # 全局标量：复用实时快照 + spotlight（成本低）
        ov: dict = {}
        sp: dict = {}
        inters: dict = {}
        if needed & set(OBS_GLOBAL) or needed & set(OBS_STRUCTURED):
            try:
                snap = rt.collector.snapshot() if rt.collector else {}
                ov = snap.get("overall", {})
                inters = snap.get("intersections", {})
            except Exception:  # noqa: BLE001
                pass
            if needed & {"completion_rate", "departed"}:
                try:
                    sp = rt.spotlight()
                except Exception:  # noqa: BLE001
                    pass
        if "vehicle_count" in needed:
            out["vehicle_count"] = ov.get("vehicle_count", 0)
        if "avg_speed" in needed:
            out["avg_speed"] = ov.get("avg_speed", 0.0)
        if "avg_waiting" in needed:
            out["avg_waiting"] = ov.get("avg_waiting_time", 0.0)
        if "avg_queue" in needed:
            out["avg_queue"] = ov.get("avg_queue_length", 0.0)
        if "total_throughput" in needed:
            out["total_throughput"] = ov.get("total_throughput", 0)
        if "departed" in needed:
            out["departed"] = sp.get("total_departed", 0)
        if "completion_rate" in needed:
            out["completion_rate"] = sp.get("completion_rate", 0.0)

        if "tls_queues" in needed:
            out["tls_queues"] = {t: v.get("queue_length", 0) for t, v in inters.items()}
        if "tls_waiting" in needed:
            out["tls_waiting"] = {t: v.get("waiting_time", 0) for t, v in inters.items()}
        if "tls_phases" in needed:
            out["tls_phases"] = {t: v.get("current_phase", 0) for t, v in inters.items()}

        if needed & {"edge_flows", "edge_queues", "congestion"} and rt.session is not None:
            eng = rt.session.engine
            flows: dict[str, int] = {}
            try:
                for eid in eng.get_edge_ids():
                    if eid.startswith(":"):
                        continue
                    flows[eid] = eng.get_edge_queue(eid)
            except Exception:  # noqa: BLE001
                flows = {}
            if "edge_flows" in needed:
                out["edge_flows"] = flows
            if "edge_queues" in needed:
                # 口径：以边内当前车辆数近似排队（轻量，每边 1 次 TraCI）
                out["edge_queues"] = dict(flows)
            if "congestion" in needed:
                total = sum(flows.values())
                vcount = ov.get("vehicle_count", 0) or 1
                out["congestion"] = round(min(1.0, total / vcount), 4)
        return out


def list_algorithm_ids(runtime) -> list[str]:
    """按注册表列出所有算法 id（未激活也列出，schema 中 active=false）。"""
    from app.schemes import registry
    return list(registry.list_schemes() and [s["id"] for s in registry.list_schemes()])


def get_adapter(runtime, algorithm_id: str) -> AlgorithmAdapter:
    from app.schemes import registry
    if not registry.has_scheme(algorithm_id):
        raise KeyError(algorithm_id)
    scheme = None
    if runtime.scheme is not None and runtime.scheme.name == algorithm_id:
        scheme = runtime.scheme
    return AlgorithmAdapter(runtime, algorithm_id, scheme)
