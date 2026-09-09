/**
 * 路口车流冲突检测模块
 *
 * 独立于 UI 的纯逻辑模块，可复用于：
 *   1. 当前编排的冲突检测与展示
 *   2. 枚举最优编排方案时筛除不合理候选
 *
 * 冲突判定分为两层：
 *   A. 同出口汇入冲突 — 两股车流进入同一条道路（含右转汇入）
 *   B. 路径交叉冲突   — 两股车流在路口中心区域交叉
 *
 * 方向定义（以 North 为例，顺时针次序 N→E→S→W）：
 *   straight : 直行，出口 = 对向 (N→S)
 *   left     : 左转，出口 = 逆时针邻向 (N→W)
 *   right    : 右转，出口 = 顺时针邻向 (N→E)
 */

// ─── 基础常量 ────────────────────────────────────────────────
const APPROACHES = ['North', 'East', 'South', 'West'];
const SIMPLE_DIRS = ['left', 'straight', 'right'];

const OPPOSITE = { North: 'South', South: 'North', East: 'West', West: 'East' };
const CLOCKWISE = { North: 'East', East: 'South', South: 'West', West: 'North' };
const COUNTER_CLOCKWISE = { North: 'West', East: 'North', South: 'East', West: 'South' };

// ─── 方向分解 ────────────────────────────────────────────────

/** 将 allowed_directions 数组展开为简单方向集合 {left, straight, right} */
function decomposeDirections(allowedDirs) {
  const set = new Set();
  for (const d of allowedDirs) {
    if (d === 'left' || d === 'straight' || d === 'right') {
      set.add(d);
    } else if (d === 'left_straight') {
      set.add('left'); set.add('straight');
    } else if (d === 'straight_right') {
      set.add('straight'); set.add('right');
    } else if (d === 'left_straight_right') {
      set.add('left'); set.add('straight'); set.add('right');
    }
  }
  return set;
}

/** 计算给定进口+简单方向的出口道路 */
function exitApproach(approach, dir) {
  if (dir === 'straight') return OPPOSITE[approach];
  if (dir === 'left')     return COUNTER_CLOCKWISE[approach];
  if (dir === 'right')    return CLOCKWISE[approach];
  return null;
}

// ─── 核心冲突判断 ────────────────────────────────────────────

/**
 * 判断两个简单方向在两个不同进口下是否冲突。
 * 调用方保证 app1 !== app2 且 dir1, dir2 ∈ {left, straight, right}。
 */
function simpleConflict(app1, dir1, app2, dir2) {
  if (app1 === app2) return false;

  const exit1 = exitApproach(app1, dir1);
  const exit2 = exitApproach(app2, dir2);

  // A. 同出口汇入冲突：两股车流汇入同一道路
  if (exit1 === exit2) return true;

  // B. 路径交叉冲突（仅当两方均非纯右转时检查）
  //     右转车辆贴边行驶，不穿过路口中心
  if (dir1 === 'right' || dir2 === 'right') return false;

  const isOpposite = OPPOSITE[app1] === app2;
  const isPerpendicular = !isOpposite;

  // 直行 vs 直行：仅垂直方向交叉（对向直行平行，不交叉）
  if (dir1 === 'straight' && dir2 === 'straight') {
    return isPerpendicular;
  }

  // 左转 vs 直行：左转路径必然穿过异向进口的直行路径
  if ((dir1 === 'left' && dir2 === 'straight') || (dir1 === 'straight' && dir2 === 'left')) {
    return true;
  }

  // 左转 vs 左转：不交叉
  //   对向左转各走路口一侧（N_left→W 走 NW，S_left→E 走 SE）
  //   垂直左转路径无交集（N_left→W 走 NW，E_left→N 走 NE）
  if (dir1 === 'left' && dir2 === 'left') {
    return false;
  }

  return false;
}

/**
 * 判断两条车道是否存在车流冲突。
 * @param {{approach: string, allowed_directions: string[]}} lane1
 * @param {{approach: string, allowed_directions: string[]}} lane2
 * @returns {boolean}
 */
function lanesConflict(lane1, lane2) {
  // 同进口不判冲突
  if (lane1.approach === lane2.approach) return false;

  const dirs1 = decomposeDirections(lane1.allowed_directions);
  const dirs2 = decomposeDirections(lane2.allowed_directions);

  // 只要存在一对冲突简单方向即判定冲突
  for (const d1 of dirs1) {
    for (const d2 of dirs2) {
      if (simpleConflict(lane1.approach, d1, lane2.approach, d2)) {
        return true;
      }
    }
  }
  return false;
}

// ─── 相位级检测 ──────────────────────────────────────────────

/**
 * 检测单个相位内的冲突。
 * @param {object} intersection - 路口数据 { lanes, phases, approaches }
 * @param {number} phaseIndex      - 相位索引
 * @param {object} laneColorMap    - 车道颜色映射 { laneId: { phaseIdx: 'green'|'red' } }
 *                                   传 null 则使用 signal_groups 判断
 * @returns {{ lane1, lane2, reason }[]} 冲突列表
 */
function detectPhaseConflicts(intersection, phaseIndex, laneColorMap) {
  const conflicts = [];
  const phase = intersection.phases[phaseIndex];
  if (!phase) return conflicts;

  const alwaysGreen = new Set(intersection.always_green || []);

  let greenLanes;
  if (laneColorMap) {
    greenLanes = intersection.lanes.filter(lane => {
      if (alwaysGreen.has(lane.signal_group)) return false;
      const lc = laneColorMap[lane.id];
      return lc && lc[phaseIndex] === 'green';
    });
  } else {
    const greenSGs = new Set(phase.signal_groups || []);
    greenLanes = intersection.lanes.filter(lane => {
      if (alwaysGreen.has(lane.signal_group)) return false;
      return greenSGs.has(lane.signal_group);
    });
  }

  for (let i = 0; i < greenLanes.length; i++) {
    for (let j = i + 1; j < greenLanes.length; j++) {
      if (lanesConflict(greenLanes[i], greenLanes[j])) {
        conflicts.push({
          lane1: greenLanes[i],
          lane2: greenLanes[j],
          reason: conflictReason(greenLanes[i], greenLanes[j])
        });
      }
    }
  }
  return conflicts;
}

/**
 * 检测所有相位的冲突。
 * @returns {{ phaseIdx, phaseName, conflicts: {lane1, lane2, reason}[] }[]}
 */
function detectAllConflicts(intersection, laneColorMap) {
  const results = [];
  for (let pi = 0; pi < intersection.phases.length; pi++) {
    const conflicts = detectPhaseConflicts(intersection, pi, laneColorMap);
    if (conflicts.length > 0) {
      results.push({
        phaseIdx: pi,
        phaseName: intersection.phases[pi].name,
        conflicts
      });
    }
  }
  return results;
}

// ─── 辅助 ────────────────────────────────────────────────────

const DIR_LABELS = { left: '左转', straight: '直行', right: '右转' };
const APPROACH_LABELS = { North: '北', East: '东', South: '南', West: '西' };

/** 生成人类可读的冲突原因 */
function conflictReason(lane1, lane2) {
  const dirs1 = [...decomposeDirections(lane1.allowed_directions)].map(d => DIR_LABELS[d] || d).join('+');
  const dirs2 = [...decomposeDirections(lane2.allowed_directions)].map(d => DIR_LABELS[d] || d).join('+');
  const a1 = APPROACH_LABELS[lane1.approach] || lane1.approach;
  const a2 = APPROACH_LABELS[lane2.approach] || lane2.approach;
  return `${a1}向${dirs1}(${lane1.id}) ↔ ${a2}向${dirs2}(${lane2.id})`;
}

/** 判断两条车道是否互为冲突（便捷方法） */
function checkLanePair(lane1, lane2) {
  return {
    conflict: lanesConflict(lane1, lane2),
    reason: lanesConflict(lane1, lane2) ? conflictReason(lane1, lane2) : null
  };
}

// ─── 导出（模块）──────────────────────────────────────────────
if (typeof window !== 'undefined') {
  window.ConflictDetector = {
    decomposeDirections,
    exitApproach,
    simpleConflict,
    lanesConflict,
    detectPhaseConflicts,
    detectAllConflicts,
    conflictReason,
    checkLanePair,
    APPROACHES,
    OPPOSITE,
    CLOCKWISE,
    COUNTER_CLOCKWISE,
    DIR_LABELS,
    APPROACH_LABELS
  };
}
