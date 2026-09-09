"""路口车流冲突检测（Python 移植自 evaluator/conflict_detection.js）。

两层冲突判定：
  A. 同出口汇入冲突 — 两股车流进入同一条道路（含右转汇入）
  B. 路径交叉冲突   — 两股车流在路口中心区域交叉

方向定义（以 North 为例，顺时针 N→E→S→W）：
  straight : 直行，出口 = 对向 (N→S)
  left     : 左转，出口 = 逆时针邻向 (N→W)
  right    : 右转，出口 = 顺时针邻向 (N→E)

与 JS 版语义一致；generate_safe_timing 额外处理"单车道单 link 绿灯"与
右转-左转角部冲突的保守判定。
"""

APPROACHES = ["North", "East", "South", "West"]
SIMPLE_DIRS = ["left", "straight", "right"]

OPPOSITE = {"North": "South", "South": "North", "East": "West", "West": "East"}
CLOCKWISE = {"North": "East", "East": "South", "South": "West", "West": "North"}
COUNTER_CLOCKWISE = {"North": "West", "East": "North", "South": "East", "West": "South"}

_DIR_EXPAND = {
    "left": ["left"], "straight": ["straight"], "right": ["right"],
    "left_straight": ["left", "straight"],
    "straight_right": ["straight", "right"],
    "left_straight_right": ["left", "straight", "right"],
}


def decompose_directions(allowed_dirs):
    """展开 allowed_directions 为简单方向集合 {left, straight, right}。"""
    out = set()
    for d in allowed_dirs or []:
        out.update(_DIR_EXPAND.get(d, []))
    return out


def exit_approach(approach, direction):
    if direction == "straight":
        return OPPOSITE.get(approach)
    if direction == "left":
        return COUNTER_CLOCKWISE.get(approach)
    if direction == "right":
        return CLOCKWISE.get(approach)
    return None


def simple_conflict(app1, dir1, app2, dir2, conservative_right_left=True):
    """两个简单方向在不同进口下是否冲突（调用方保证 app1 != app2）。"""
    if app1 == app2:
        return False
    exit1 = exit_approach(app1, dir1)
    exit2 = exit_approach(app2, dir2)
    if exit1 is None or exit2 is None:
        return True  # 无法判定方向时保守视为冲突

    # A. 同出口汇入冲突
    if exit1 == exit2:
        return True

    # B. 路径交叉冲突
    #    保守增强：右转 vs 对向左转存在角部冲突（JS 原版未判，这里可开关）
    if conservative_right_left and {dir1, dir2} == {"right", "left"}:
        return True
    if dir1 == "right" or dir2 == "right":
        return False

    is_opposite = OPPOSITE[app1] == app2
    if dir1 == "straight" and dir2 == "straight":
        return not is_opposite
    if {dir1, dir2} == {"left", "straight"}:
        return True
    if dir1 == "left" and dir2 == "left":
        return False
    return False


def lanes_conflict(lane1, lane2, conservative_right_left=True):
    """两条车道（{approach, allowed_directions}）是否冲突。同进口不判。"""
    if lane1.get("approach") == lane2.get("approach"):
        return False
    dirs1 = decompose_directions(lane1.get("allowed_directions"))
    dirs2 = decompose_directions(lane2.get("allowed_directions"))
    for d1 in dirs1:
        for d2 in dirs2:
            if simple_conflict(lane1["approach"], d1, lane2["approach"], d2,
                               conservative_right_left):
                return True
    return False


def conflict_reason(lane1, lane2):
    dir_labels = {"left": "左转", "straight": "直行", "right": "右转"}
    app_labels = {"North": "北", "East": "东", "South": "南", "West": "西"}
    d1 = "+".join(dir_labels.get(d, d) for d in decompose_directions(lane1.get("allowed_directions")))
    d2 = "+".join(dir_labels.get(d, d) for d in decompose_directions(lane2.get("allowed_directions")))
    a1 = app_labels.get(lane1["approach"], lane1["approach"])
    a2 = app_labels.get(lane2["approach"], lane2["approach"])
    return f"{a1}向{d1}({lane1.get('id')}) vs {a2}向{d2}({lane2.get('id')})"
