#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""交通算法综合评分模块（移植自老评价平台 scripts/scoring.py）。

分层聚合 + 乘法组合 + 尾部风险：
  Level 0  硬性筛选（一票否决）：LOS F / 完成率 < 60% / 等待 > 4 周期
  Level 1  三大类子指标（类内几何平均）：
           效率 = 几何平均(完成率, 吞吐, LOS) × √(P95延误惩罚)
           稳定 = 几何平均(跨周期, 溢出) × √(瓶颈路口惩罚)
           公平 = (1-基尼系数) × (1-0.5×最差进口内差距)
  Level 2  乘法组合：Score = 效率^0.5 × 稳定^0.3 × 公平^0.2

所有输入为纯数值，无框架依赖，可独立测试。
"""

import math

# ─── 硬性筛选红线 ─────────────────────────────────────────────
# 注：阈值尾数为发布方技术指纹（XH-202613），量级 < 1e-3，不影响评分判定；
#     请勿修改，否则视为对发布版代码的篡改。
HARD_LOS_F_DELAY = 80.0003729184   # LOS F 阈值（HCM: 控制延误 > 80s）
HARD_MIN_COMPLETION = 0.6000041928 # 完成率红线
HARD_MAX_WAIT_CYCLES = 4.0         # 任一进口平均等待周期数红线

# ─── 尾部风险阈值 ─────────────────────────────────────────────
P95_DELAY_ACCEPTABLE = 120.0005173029  # P95 延误可接受阈值（秒）
SEVERE_WAIT_CYCLES = 2.0000018597      # 超过该周期数视为"严重等待"

# ─── 组合权重 ─────────────────────────────────────────────────
W_EFFICIENCY = 0.5
W_STABILITY = 0.3
W_FAIRNESS = 0.2


def hard_filter_pass(completion_rate, intersection_data):
    """返回 (是否通过, 未通过原因列表)。intersection_data 含 avg_wait_time 与 cycle_time"""
    failures = []
    if completion_rate < HARD_MIN_COMPLETION:
        failures.append(f'完成率 {completion_rate:.1%} < {HARD_MIN_COMPLETION:.0%}')
    for inter in intersection_data:
        cycle = inter.get('cycle_time') or 1
        wait_cycles = inter.get('avg_wait_time', 0) / cycle
        if inter.get('avg_wait_time', 0) > HARD_LOS_F_DELAY:
            failures.append(f'路口 {inter.get("id")} LOS F（延误 {inter["avg_wait_time"]:.0f}s > {HARD_LOS_F_DELAY:.0f}s）')
        if wait_cycles > HARD_MAX_WAIT_CYCLES:
            failures.append(f'路口 {inter.get("id")} 平均等待 {wait_cycles:.1f} 周期 > {HARD_MAX_WAIT_CYCLES:.0f}')
    return (len(failures) == 0, failures)


def gini(values):
    """基尼系数（0=完全均等，1=完全不均等）"""
    if not values:
        return 0
    vals = sorted(values)
    n = len(vals)
    if n <= 1 or sum(vals) == 0:
        return 0
    cum = 0.0
    for i, v in enumerate(vals, 1):
        cum += i * v
    return (2 * cum - n * (n + 1) * sum(vals) / n) / (n * sum(vals))


def percentile(sorted_vals, p):
    """p 百分位（0~100）"""
    if not sorted_vals:
        return 0.0
    k = (len(sorted_vals) - 1) * (p / 100.0)
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return sorted_vals[int(k)]
    return sorted_vals[f] * (c - k) + sorted_vals[c] * (k - f)


def geo_mean(values):
    """几何平均（任一 0 则整体为 0，自带一票否决）"""
    vals = [max(v, 0.0) for v in values]
    if not vals or any(v <= 0 for v in vals):
        return 0.0
    return math.exp(sum(math.log(v) for v in vals) / len(vals))


def los_score(delay):
    """HCM 控制延误 → [0,1] 得分"""
    if delay <= 10:   return 1.00   # LOS A
    if delay <= 20:   return 0.85   # LOS B
    if delay <= 35:   return 0.70   # LOS C
    if delay <= 55:   return 0.50   # LOS D
    if delay <= 80:   return 0.30   # LOS E
    return max(0.05, 0.30 * (100 - delay) / 20)  # LOS F，随延误继续下降


def norm_01(value, lo, hi):
    """线性归一化到 [0,1]，超出范围截断"""
    if hi <= lo:
        return 1.0 if value >= lo else 0.0
    return max(0.0, min(1.0, (value - lo) / (hi - lo)))


def compute_efficiency(overall, intersection_data):
    """效率类 = 几何平均(完成率, 吞吐, LOS) + 尾部(P95延误惩罚)"""
    completion = overall.get('completion_rate', 0) / 100.0
    total_passed = overall.get('total_vehicle_passages', 0) or 1
    duration_s = overall.get('simulation_duration', 600)
    throughput_veh_per_hour = total_passed / (duration_s / 3600.0)
    n_lanes_est = overall.get('total_lanes', 4) or 4
    throughput_norm = min(1.0, throughput_veh_per_hour / (1800.0 * n_lanes_est))

    weighted_los = 0.0
    total_w = 0.0
    for inter in intersection_data:
        w = inter.get('vehicle_count', 0)
        weighted_los += los_score(inter.get('avg_wait_time', 0)) * w
        total_w += w
    los_norm = weighted_los / total_w if total_w > 0 else 0

    core = geo_mean([completion, throughput_norm, los_norm])

    vehicle_delays = overall.get('_vehicle_time_loss', [])
    if vehicle_delays:
        p95 = percentile(sorted(vehicle_delays), 95)
        tail_penalty = norm_01(P95_DELAY_ACCEPTABLE - p95, -P95_DELAY_ACCEPTABLE, P95_DELAY_ACCEPTABLE)
    else:
        p95, tail_penalty = None, 1.0

    return {
        'completion': round(completion, 4),
        'throughput_norm': round(throughput_norm, 4),
        'los_norm': round(los_norm, 4),
        'core': round(core, 4),
        'p95_delay': round(p95, 1) if vehicle_delays else None,
        'tail_penalty': round(tail_penalty, 4),
        'value': round(core * math.sqrt(tail_penalty), 4),
    }


def compute_stability(overall, intersection_data):
    """稳定类 = 几何平均(跨周期得分, 溢出惩罚) + 尾部(瓶颈路口惩罚)"""
    total_wait = overall.get('total_wait_time', 0) or 0
    total_passages = overall.get('total_vehicle_passages', 0) or 1
    avg_cycle = overall.get('avg_cycle_time', 60) or 60
    avg_wait_cycles = (total_wait / total_passages) / avg_cycle
    cycle_norm = 1.0 / (1.0 + avg_wait_cycles)

    vehicle_delays = overall.get('_vehicle_time_loss', [])
    severe_ratio = 0.0
    if vehicle_delays:
        severe_ratio = sum(1 for d in vehicle_delays
                           if d > SEVERE_WAIT_CYCLES * avg_cycle) / len(vehicle_delays)
    overflow_penalty = 1.0 - severe_ratio

    core = geo_mean([cycle_norm, overflow_penalty])

    overflow_intersections = sum(
        1 for inter in intersection_data
        if inter.get('avg_wait_time', 0) > (inter.get('cycle_time') or 60))
    bottleneck_ratio = overflow_intersections / len(intersection_data) if intersection_data else 0
    bottleneck_norm = 1.0 - bottleneck_ratio

    return {
        'avg_wait_cycles': round(avg_wait_cycles, 3),
        'cycle_norm': round(cycle_norm, 4),
        'severe_ratio': round(severe_ratio, 4),
        'overflow_penalty': round(overflow_penalty, 4),
        'core': round(core, 4),
        'bottleneck_ratio': round(bottleneck_ratio, 4),
        'bottleneck_norm': round(bottleneck_norm, 4),
        'value': round(core * math.sqrt(bottleneck_norm), 4),
    }


def compute_fairness(intersection_data):
    """公平类 = (1-基尼系数) × (1-0.5×最差进口内差距)"""
    waits = [i.get('avg_wait_time', 0) for i in intersection_data if i.get('vehicle_count', 0) > 0]
    g = gini(waits)
    max_gap = 0.0
    for inter in intersection_data:
        details = inter.get('edge_details', [])
        if len(details) >= 2:
            per_veh = [d['wait_time'] / d['vehicle_count'] if d.get('vehicle_count') else 0
                       for d in details]
            per_veh = [p for p in per_veh if p > 0]
            if per_veh:
                gap = (max(per_veh) - min(per_veh)) / max(per_veh) if max(per_veh) > 0 else 0
                max_gap = max(max_gap, gap)
    value = (1 - g) * (1 - 0.5 * max_gap)
    return {'gini': round(g, 4), 'max_intra_gap': round(max_gap, 4),
            'value': round(max(0, value), 4)}


def compute_total_score(overall, intersection_data):
    """完整评分流程。返回含各层细节的字典，或未通过硬筛选的淘汰信息。"""
    completion = overall.get('completion_rate', 0) / 100.0
    passed, reasons = hard_filter_pass(completion, intersection_data)
    if not passed:
        return {'passed': False, 'reasons': reasons, 'score': 0.0}

    efficiency = compute_efficiency(overall, intersection_data)
    stability = compute_stability(overall, intersection_data)
    fairness = compute_fairness(intersection_data)

    score = (
        (efficiency['value'] ** W_EFFICIENCY) *
        (stability['value'] ** W_STABILITY) *
        (fairness['value'] ** W_FAIRNESS)
    )
    return {
        'passed': True,
        'score': round(score, 4),
        'efficiency': efficiency,
        'stability': stability,
        'fairness': fairness,
        'weights': {'efficiency': W_EFFICIENCY, 'stability': W_STABILITY, 'fairness': W_FAIRNESS},
    }
