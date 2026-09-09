"""官方配时 → base_network 相位程序 正式生成器 v4（最近臂匹配，完全忠于官方）。

关键修正：base_network 各 tls 的进边几何与官方 xlsx 的"东西南北"标签不是
严格正南北（如 tls1 的南臂实际几何为 SE@315）。因此方向词匹配改为
"在该 tls 的进边中找 compass8 距离最近的那条臂"：
  官方'东西'/'东'/'西' → tls 中方位最接近 E 的臂 + 最接近 W 的臂；
  官方'南北'/'南'/'北' → 最接近 N 的臂 + 最接近 S 的臂；
  '东北'/'西北'/'东南'/'西南' → 最接近对应斜向的臂（无则取两主轴间夹角小者）。

已核实前提：base_network tls '1'..'20' 与官方 demo_1..20 一一对应
（20/20 缺臂方向吻合）；官方算法范式=保相位程序、切换瞬间改时长/整程序。

用户规则：直行相放直行连接(最左 lane 之外)，左转相放最左 lane 的 l，
掉头并入左转相；右转并入直行相最右车道（同 lane0 随直行 G），无直行覆盖时给 g；
黄/全红按官方独立成相；周期完全按官方。

输出：backend/data/official_phase_programs.json
  {tls_id: {demo, conns, approach_dir(臂方位角), plans: [{period, cycle, phases}]}}
"""
import json
import math
import os
import xml.etree.ElementTree as ET
import sumolib

NET = r'C:\Users\27773\Desktop\xiongan_v5.1\networks\network\base_network.net.xml'
OFF_DIR = r'C:\Users\27773\Desktop\xiongan_v5.1\backend\data\official_plans'
OUT = r'C:\Users\27773\Desktop\xiongan_v5.1\backend\data\official_phase_programs.json'

net = sumolib.net.readNet(NET)
node_coords = {n.getID(): n.getCoord() for n in net.getNodes()}
EDGE8 = ['E', 'NE', 'N', 'NW', 'W', 'SW', 'S', 'SE']
# 主轴角度（度，0=E 逆时针到 360）
EDGE_ANGLE = {d: i * 45.0 for i, d in enumerate(EDGE8)}


def ang8(x, y, jx, jy):
    b = math.degrees(math.atan2(y - jy, x - jx)) % 360
    return EDGE8[int(round(b / 45.0)) % 8]


def approach_map(net, tls_id):
    """tls 每条进边 → (compass8 名, 方位角)。"""
    jc = node_coords.get(tls_id)
    out = {}
    if jc is None:
        return out
    for e in net.getEdges():
        if e.getToNode().getID() == tls_id:
            far = e.getFromNode().getCoord()
            b = math.degrees(math.atan2(far[1] - jc[1], far[0] - jc[0])) % 360
            out[e.getID()] = EDGE8[int(round(b / 45.0)) % 8]
    return out


def nearest_arm(app_compass, want):
    """在 app_compass(edge->8方位名) 中找方位最接近 want 的一条边。"""
    want_a = EDGE_ANGLE[want]
    best, best_d = None, 1e9
    for e, c in app_compass.items():
        d = abs(((EDGE_ANGLE[c] - want_a + 180) % 360) - 180)
        if d < best_d:
            best, best_d = e, d
    return best


def load_conns(net_path):
    root = ET.parse(net_path).getroot()
    conns = {}
    for c in root.iter('connection'):
        tl = c.get('tl')
        if tl is None:
            continue
        conns.setdefault(tl, []).append({
            'idx': int(c.get('linkIndex', 0)),
            'from': c.get('from'),
            'lane': int(c.get('fromLane') or 0),
            'to': c.get('to'),
            'dir': (c.get('dir') or '').lower()})
    for tl in conns:
        conns[tl].sort(key=lambda x: x['idx'])
    return conns


def parse_directions(name):
    """官方相位名 → 目标臂方位集合（罗盘词）。

    关键：复合方向词（东北/东西…）须优先整体命中并**从名字中移除**，
    避免短词（东/南）重复命中长词内部（如"东北"又被"东"再匹配一次）。
    例："东北西南左转"→{NE,SW}；"东西向直行"→{E,W}；"东向左右转"→{E}。
    """
    n = name
    out: set[str] = set()
    combos = [('东西', {'E', 'W'}), ('南北', {'N', 'S'}),
              ('东北', {'NE'}), ('西南', {'SW'}),
              ('东南', {'SE'}), ('西北', {'NW'})]
    singles = [('东', 'E'), ('西', 'W'), ('南', 'S'), ('北', 'N')]
    changed = True
    while changed and n:
        changed = False
        for tok, cset in combos:
            if tok in n:
                n = n.replace(tok, '', 1)
                out |= cset
                changed = True
                break
        if changed:
            continue
        for tok, c in singles:
            if tok in n:
                n = n.replace(tok, '', 1)
                out.add(c)
                changed = True
                break
    if not out:
        out = set(EDGE8)      # 缺方向词（如"放行"）→ 全臂
    return out


def parse_turns(name):
    turns = set()
    if '左' in name and '直' in name:
        turns = {'s', 'l'}
    elif '左' in name and '右' in name:
        turns = {'s', 'l', 'r'}
    elif '左' in name:
        turns = {'l'}
    elif '右' in name and '直' in name:
        turns = {'s', 'r'}
    elif '右' in name:
        turns = {'r'}
    elif '直' in name or '放行' in name or '通行' in name or '行' in name:
        turns = {'s'}
    else:
        turns = {'s'}
    return turns


def build_state(tls_id, conns, arms, dirs_want, turns):
    """构建一个相位 state 串。arms: from_edge->compass8。"""
    n = len(conns)
    target_edges = set()
    for want in dirs_want:
        e = nearest_arm(arms, want)
        if e:
            target_edges.add(e)
    st = ['r'] * n
    greens = set()
    for k, c in enumerate(conns):
        if c['from'] in target_edges:
            d = c['dir']
            if d in turns:
                greens.add(k)
            elif d == 't' and 'l' in turns:   # 掉头并入左转
                greens.add(k)
            elif d == 'r' and 's' in turns and c['lane'] == 0:
                greens.add(k)                  # 最右车道右转并入直行
    for k in greens:
        st[k] = 'G'
    # 剩余右转：让行常绿（不另设相位）
    for k, c in enumerate(conns):
        if c['dir'] == 'r' and st[k] == 'r' and not ('r' in turns and c['from'] in target_edges):
            st[k] = 'g'
    return ''.join(st)


def build_all():
    conns = load_conns(NET)
    out = {}
    for i in range(1, 21):
        tid = str(i)
        off = os.path.join(OFF_DIR, f'demo_{i}.json')
        if not os.path.isfile(off) or tid not in conns:
            continue
        data = json.load(open(off, encoding='utf-8'))
        arms = approach_map(net, tid)
        ct = conns[tid]
        n = len(ct)
        plans = []
        for plan in data.get('plans', []):
            built = []
            for ph in plan.get('phases', []):
                name = ph.get('name') or ''
                g = ph.get('green')
                if g is None:
                    continue
                dirs_want = parse_directions(name)
                turns = parse_turns(name)
                st = build_state(tid, ct, arms, dirs_want, turns)
                built.append((st, float(g), 'green'))
                if ph.get('yellow'):
                    # 黄灯只针对该相位正在放行的绿灯（G）变黄，其余方向保持红，
                    # 与真实信号机一致（不做整路口全黄）。
                    yl = ''.join('y' if ch == 'G' else ch for ch in st)
                    built.append((yl, float(ph['yellow']), 'yellow'))
                if ph.get('all_red'):
                    built.append(('r' * n, float(ph['all_red']), 'red'))
            # 该档 EW/NS 总绿时（绿灯相位按 state 中 G/g 连接的进边 compass 累加）
            ew_g = ns_g = 0.0
            for st, dur, kind in built:
                if kind != 'green':
                    continue
                for k, ch in enumerate(st):
                    if ch == 'G':
                        comp = arms.get(ct[k]['from'], '')
                        if comp in ('E', 'NE', 'SE'):
                            ew_g += dur
                        elif comp in ('W', 'NW', 'SW'):
                            ew_g += dur
                        elif comp in ('N', 'S'):
                            ns_g += dur
            # 归一为占比方便规则比对
            tot = ew_g + ns_g or 1.0
            plans.append({
                'period': plan['period'],
                'range': plan.get('range'),
                'cycle_official': plan.get('cycle'),
                'cycle_built': round(sum(p[1] for p in built), 1),
                'ew_ratio': round(ew_g / tot, 3),
                'ns_ratio': round(ns_g / tot, 3),
                'ew_g': round(ew_g, 1),
                'ns_g': round(ns_g, 1),
                'phases': [{'state': s, 'dur': d, 'kind': k} for s, d, k in built],
            })
        out[tid] = {
            'demo': i,
            'conns': [{'from': c['from'], 'lane': c['lane'], 'dir': c['dir']} for c in ct],
            'arms': {e: arms.get(e) for e in sorted(arms)},
            'plans': plans,
        }
    with open(OUT, 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f'v4 written -> {OUT}')
    ok = 0
    for tid in sorted(out, key=int):
        v = out[tid]
        diffs = [(p['period'], p['cycle_official'], p['cycle_built'])
                 for p in v['plans']
                 if p['cycle_official'] and abs(p['cycle_official'] - p['cycle_built']) > 0.01]
        ok += (not diffs)
        print(f"tls {tid}: arms={ {e: v['arms'][e] for e in v['arms']} } "
              f"periods={[(p['period'], len(p['phases'])) for p in v['plans']]} "
              f"cycle_diff={diffs}")
    print('cycle-exact count:', ok, '/20')


if __name__ == '__main__':
    build_all()
