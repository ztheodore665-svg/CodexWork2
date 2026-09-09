"""路网图：有向图结构 + 受限边管理 + 路线连续性验证。"""


class RoadNetwork:
    def __init__(self, ctx):
        self.ctx = ctx
        self.engine = ctx.engine
        self._restricted: dict[str, str] = {}
        self._node_ids: set[str] = set()
        self._node_ids.update(self._collect_nodes())

    def _collect_nodes(self) -> list[str]:
        """从 engine 收集节点信息（尽力而为，缺失时为空集）。"""
        nodes: set[str] = set()
        try:
            get_node_ids = getattr(self.engine, "get_node_ids", None)
            if get_node_ids is not None:
                nodes.update(get_node_ids())
        except Exception:  # noqa: BLE001
            pass
        return list(nodes)

    def node_count(self) -> int:
        return len(self._node_ids) or len(self.engine.get_edge_ids())

    def edge_count(self) -> int:
        return len(self.engine.get_edge_ids())

    # ── 受限边 ──────────────────────────────────────────────

    def add_restricted_edge(self, edge_id: str, reason: str = "restricted") -> None:
        self._restricted[edge_id] = reason

    def remove_restricted_edge(self, edge_id: str) -> None:
        self._restricted.pop(edge_id, None)

    def clear_restricted_edges(self) -> None:
        self._restricted.clear()

    def is_restricted(self, edge_id: str) -> bool:
        return edge_id in self._restricted

    def restricted_edges(self) -> dict[str, str]:
        return dict(self._restricted)

    # ── 路线验证 ────────────────────────────────────────────

    def verify_route(self, edges: list[str]) -> bool:
        if not edges:
            return False
        if any(self.is_restricted(e) for e in edges):
            return False
        for i in range(len(edges) - 1):
            succ = set(self.engine.get_edge_successors(edges[i]))
            if edges[i + 1] not in succ:
                return False
        return True

    def free_flow_time(self, edge_id: str) -> float:
        length = self.engine.get_edge_length(edge_id)
        limit = self.engine.get_edge_speed_limit(edge_id)
        return length / limit if limit > 0 else 0.0
