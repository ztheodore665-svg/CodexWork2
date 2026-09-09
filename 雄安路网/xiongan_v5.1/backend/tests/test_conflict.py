from app.core.conflict import (
    decompose_directions,
    exit_approach,
    lanes_conflict,
    simple_conflict,
)


def _lane(approach, dirs, lid=None):
    return {"id": lid or f"{approach}", "approach": approach,
            "allowed_directions": dirs}


# ── 方向映射 ────────────────────────────────────────────────

def test_exit_approach():
    assert exit_approach("North", "straight") == "South"
    assert exit_approach("North", "left") == "West"
    assert exit_approach("North", "right") == "East"


def test_decompose_directions():
    assert decompose_directions(["left_straight"]) == {"left", "straight"}
    assert decompose_directions(["left_straight_right"]) == {"left", "straight", "right"}


# ── 规则 A：同出口汇入 ──────────────────────────────────────

def test_same_exit_merge_conflict():
    # N 左转→W 与 E 直行→W 汇入 W 路
    assert simple_conflict("North", "left", "East", "straight") is True
    # N 右转→E 与 S 左转→E 汇入 E 路
    assert simple_conflict("North", "right", "South", "left") is True


# ── 规则 B：路径交叉 ────────────────────────────────────────

def test_straight_straight():
    assert simple_conflict("North", "straight", "East", "straight") is True   # 垂直
    assert simple_conflict("North", "straight", "South", "straight") is False  # 对向平行


def test_left_vs_straight_always():
    assert simple_conflict("North", "left", "East", "straight") is True
    assert simple_conflict("East", "straight", "North", "left") is True


def test_left_vs_left_no_conflict():
    assert simple_conflict("North", "left", "South", "left") is False


def test_right_is_permissive():
    # 右转 vs 直行：默认非冲突（贴边）
    assert simple_conflict("North", "right", "South", "straight") is False


def test_right_vs_left_conservative():
    # 保守增强：右转 vs 左转判冲突；关闭时与 JS 原版一致为 False
    assert simple_conflict("North", "right", "East", "left") is True
    assert simple_conflict("North", "right", "East", "left",
                           conservative_right_left=False) is False


# ── lanes_conflict ──────────────────────────────────────────

def test_lanes_conflict_compound_directions():
    # 北进口"左转直行"车道 vs 东进口直行：N_left→W 与 E_straight→W 同出口 → 冲突
    assert lanes_conflict(_lane("North", ["left_straight"]),
                          _lane("East", ["straight"])) is True


def test_lanes_same_approach_no_conflict():
    assert lanes_conflict(_lane("North", ["left"]),
                          _lane("North", ["straight"])) is False
