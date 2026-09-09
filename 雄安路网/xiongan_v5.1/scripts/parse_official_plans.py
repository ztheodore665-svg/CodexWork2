# -*- coding: utf-8 -*-
"""
parse_official_plans.py —— 雄安竞赛官方 20 个路口的配时/流量 xlsx -> 结构化 JSON 方案库
========================================================================================

数据来源
--------
    赛题资料官方 Excel（供赛原始数据）：
        C:\\Users\\27773\\Desktop\\赛题资料\\路口数据\\<N>\\路口数据\\demo_<N>流量和交叉口配时方案.xlsx
        (N = 1..20)
    每个工作簿通常含两个 sheet：
        「流量数据」      —— 各进口分转向的 15 分钟交通量（pcu）；
        「信号配时数据」  —— 分时段的信号配时方案（相位级）。
    注意：个别文件（demo_7 / demo_11）的配时 sheet 名为「配信号时数据」，
    因此按“名称含 配时/信号 且 不含 流量”匹配 sheet，而不是写死 sheet 名；
    若匹配不到唯一候选，则把全部 sheet 名打印到控制台并跳过该文件。

输出
----
    每个路口一个 JSON 文件（UTF-8, ensure_ascii=False, indent=2）：
        C:\\Users\\27773\\Desktop\\xiongan_v5.1\\backend\\data\\official_plans\\demo_<N>.json

输出 JSON schema
----------------
    {
      "demo":   "demo_1",                                  # 路口标识
      "source": "路口数据/1/路口数据/demo_1流量和交叉口配时方案.xlsx",  # 相对数据来源路径
      "plans": [                                           # 信号配时（来自「信号配时数据」sheet）
        {
          "period": "早高峰", "range": "7:00-9:00",        # 时段名 / 时段范围(原文)
          "cycle": 160,                                    # 周期总时长(s)，缺失时=各相位时长之和
          "phases": [
            {"no": 1, "name": "东西向直行", "green": 38, "yellow": 3, "all_red": 2},
            ...                                            # no=相位编号, name=相位名称
          ]
        }, ...
      ],
      "flow": [                                            # 流量（来自「流量数据」sheet）
        {
          "period": "早高峰", "range": "7:00-9:00",        # range 取自配时表同名时段；缺失时按首个/末个 bin 推导
          "bins": [                                        # 每 15 分钟一个 bin
            {
              "start": "07:00", "end": "07:15",
              "east":  {"left": 56, "straight": 45, "right": 38},
              "west":  {"left": 42, "straight": 30, "right": 39},
              "north": {"left": 26, "straight": 60, "right": 28},
              "south": {"left": 58, "straight": 42, "right": 34}
            }, ...
          ],
          "totals": {"east": 1127, "west": 894, "north": 923, "south": 1054}
                    # 各进口总流量，取自「配置参数/总流量」行（各进口分组的首列）；
                    # 若该进口无总流量数值则回退为该进口各 bin 之和；仍无数据列则记 0。
        }, ...
      ]
    }

字段语义与容错规则
------------------
    1. 数值一律转 int：
       - 配时 green/yellow/all_red 单元格为空(None)时输出 null（不转 0）；
       - 流量 bin / totals 数值为空、为文本、或该进口/转向在表内无对应列时，一律输出 0。
    2. 「流量数据」按 早高峰/平峰/晚高峰 三块解析；每块的进出口列位置由该块自己的表头
       （进口行 + 左转/直行/右转行）动态推导，不写死列号，因此能容忍个别文件列偏移、
       多出的第五进口（如 demo_9 的“东北进口”，按约定忽略、只取 东/西/北/南）。
    3. 一个路口不同时段的相位个数可以不同（如 demo_4 平峰只有 3 相）；配时块之间可能存在
       空行，解析按“相位编号为数值的行”收集相位，不受空行影响。
    4. demo_13 晚高峰第 4 相位 黄灯/全红 为 None -> JSON 中输出 null。
    5. 若某时段在配时表中找不到同名时段（理论上不发生），flow.range 按该块首个/末个 bin 时间推导。
    6. 幂等：每次运行会整体覆盖输出目录中的 demo_*.json，可反复执行。

运行方式（避免中文乱码）：
    python -X utf8 parse_official_plans.py        # 工作目录任意，脚本路径基于 __file__ 定位
"""

import datetime
import json
import os
import re
import sys

# ---------------------------------------------------------------------------
# 路径配置
# ---------------------------------------------------------------------------
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.dirname(_SCRIPT_DIR)          # ...\xiongan_v5.1
SRC_ROOT = r"C:\Users\27773\Desktop\赛题资料\路口数据"
OUT_DIR = os.path.join(BASE_DIR, "backend", "data", "official_plans")

FLOW_PERIODS = ("早高峰", "平峰", "晚高峰")       # 固定三种时段，保持顺序
# 官方三种时段的缺省范围（仅当配时表 / bin 都无法给出 range 时兜底）
DEFAULT_RANGE = {"早高峰": "7:00-9:00", "平峰": "14:30-16:30", "晚高峰": "17:30-19:30"}
DIRS = ("east", "west", "north", "south")
DIR_LABELS = {"east": "东进口", "west": "西进口", "north": "北进口", "south": "南进口"}
TURN_TOKENS = ("左转", "直行", "右转")
TURN_KEYS = ("left", "straight", "right")


def log(msg=""):
    """控制台输出（脚本使用 python -X utf8 运行，中文不会乱码）。"""
    print(msg)


# ---------------------------------------------------------------------------
# 数值 / 文本小工具
# ---------------------------------------------------------------------------
def _to_int_or_none(v):
    """把单元格数值安全转 int；None / 非数值 / bool 返回 None。"""
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return int(v)
    if isinstance(v, str):
        s = v.strip()
        if s == "":
            return None
        try:
            return int(float(s))
        except (ValueError, TypeError):
            return None
    return None


def _to_int0(v):
    """流量用：空值/非数值一律 0。"""
    x = _to_int_or_none(v)
    return 0 if x is None else x


def _row(ws_row, width=24):
    """把 openpyxl 的一行元组统一补/截成固定宽度，避免索引越界。"""
    r = list(ws_row)
    if len(r) < width:
        r = r + [None] * (width - len(r))
    return r[:width]


def _cell_str(v):
    if v is None:
        return ""
    return str(v).strip()


def _normalize_time(v):
    """把时间单元格归一为 'HH:MM'；无法识别时返回 None。"""
    if v is None:
        return None
    if hasattr(v, "strftime"):                 # datetime.time / datetime.datetime
        return v.strftime("%H:%M")
    if isinstance(v, str):
        s = v.strip()
        m = re.match(r"^(\d{1,2}):(\d{1,2})(?::\d{1,2})?$", s)
        if m:
            return "%02d:%02d" % (int(m.group(1)), int(m.group(2)))
    if isinstance(v, (int, float)):            # 理论上不用；防御
        return None
    return None


def _is_time_like(v):
    return _normalize_time(v) is not None


# ---------------------------------------------------------------------------
# 1) 解析「信号配时数据」sheet -> plans
# ---------------------------------------------------------------------------
def _pick_timing_sheet(wb, sheet_names):
    """按“名称含 配时/信号 且 不含 流量”选取配时 sheet；异常时返回 None 并打印所有 sheet 名。"""
    cand = [s for s in sheet_names
            if ("配时" in s or "信号" in s) and ("流量" not in s)]
    if len(cand) == 1:
        return cand[0]
    log("  [警告] 配时 sheet 候选数 != 1（实际为 %d），全部 sheet 名如下：" % len(cand))
    for s in sheet_names:
        log("      - %r" % s)
    return None


def _parse_plans(ws):
    """从配时 sheet 解析 plans。返回 (plans, notes)。"""
    rows = [_row(r) for r in ws.iter_rows(values_only=True)]

    # ---- 找表头行（含 时段分类 + 相位编号）----
    hdr_i = None
    for i, r in enumerate(rows):
        joined = "|".join(_cell_str(c) for c in r)
        if "时段分类" in joined and "相位编号" in joined:
            hdr_i = i
            break
    if hdr_i is None:
        raise ValueError("配时表找不到含「时段分类」「相位编号」的表头行")

    hdr = rows[hdr_i]
    # 由表头文本定位各列（兼容“绿灯时长 (s)”“周期总时长(s)”等写法）
    def _find_col(keyword, fallback):
        for j, c in enumerate(hdr):
            if keyword in _cell_str(c):
                return j
        return fallback

    col_period = _find_col("时段分类", 0)
    col_range = _find_col("时段范围", 1)
    col_no = _find_col("相位编号", 2)
    col_name = _find_col("相位名称", 3)
    col_green = _find_col("绿灯", 4)
    col_yellow = _find_col("黄灯", 5)
    col_red = _find_col("全红", 6)
    col_cycle = _find_col("周期总时长", 8)

    plans, notes = [], []
    cur = None  # 当前时段块 {"period","range","cycle","phases"}

    def _close_block():
        nonlocal cur
        if cur is not None:
            if cur["cycle"] is None:   # 周期缺失兜底：各相位时长(g+y+r, None 计 0)之和
                cur["cycle"] = sum(
                    (p["green"] or 0) + (p["yellow"] or 0) + (p["all_red"] or 0)
                    for p in cur["phases"])
            plans.append(cur)
            cur = None

    for r in rows[hdr_i + 1:]:
        period_txt = _cell_str(r[col_period])
        no = r[col_no]
        if period_txt:                      # 新时段块（块首行）
            _close_block()
            cur = {
                "period": period_txt,
                "range": _cell_str(r[col_range]),
                "cycle": _to_int_or_none(r[col_cycle]),
                "phases": [],
            }
            # 块首行同时可能是第 1 相位行
            if _to_int_or_none(no) is not None:
                cur["phases"].append({
                    "no": _to_int_or_none(no),
                    "name": _cell_str(r[col_name]),
                    "green": _to_int_or_none(r[col_green]),
                    "yellow": _to_int_or_none(r[col_yellow]),
                    "all_red": _to_int_or_none(r[col_red]),
                })
        elif cur is not None and _to_int_or_none(no) is not None:
            # 相位行（行首无时段名；跨空行也能连续收集）
            cur["phases"].append({
                "no": _to_int_or_none(no),
                "name": _cell_str(r[col_name]),
                "green": _to_int_or_none(r[col_green]),
                "yellow": _to_int_or_none(r[col_yellow]),
                "all_red": _to_int_or_none(r[col_red]),
            })
    _close_block()

    # 简单自检：相位编号应当连续 1..k
    for p in plans:
        nums = [ph["no"] for ph in p["phases"]]
        if nums != list(range(1, len(nums) + 1)):
            notes.append("时段 %s 相位编号不连续: %s" % (p["period"], nums))
    return plans, notes


# ---------------------------------------------------------------------------
# 2) 解析「流量数据」sheet -> flow
# ---------------------------------------------------------------------------
def _find_flow_blocks(rows):
    """找流量块：块首行 col0 为“<时段名>…流量…数据”类文本。返回 [(行号, 时段名, 标签文本)]。"""
    blocks = []
    for i, r in enumerate(rows):
        txt = _cell_str(r[0])
        period = next((kw for kw in FLOW_PERIODS if kw in txt), None)
        if period is not None and "流量" in txt:
            blocks.append((i, period, txt))
    return blocks


def _read_block_headers(rows, start):
    """读流量块的两个表头行，返回 (方向起始列表, 列->(方向,转向)映射)。"""
    hdr_dir = rows[start + 1]   # 进口行（东进口/西进口/…，合并单元格只填首列）
    hdr_turn = rows[start + 2]  # 转向行（左转(pcu)/直行(pcu)/右转(pcu)）

    # 1) 定位 4 个主方向标签列：标签文本 == 方向+“进口”
    start_col = {}
    for j, c in enumerate(hdr_dir):
        s = _cell_str(c)
        for d, label in DIR_LABELS.items():
            if s == label and d not in start_col:
                start_col[d] = j
    # 2) 每个主方向取 (左转/直行/右转) 三列；列上转向行文本须含对应关键词
    col_map = {}   # (方向, turn_key) -> 列号
    for d in DIRS:
        if d not in start_col:
            continue
        s0 = start_col[d]
        for k, tok in enumerate(TURN_TOKENS):
            j = s0 + k
            cell = _cell_str(hdr_turn[j]) if j < len(hdr_turn) else ""
            if tok in cell:
                col_map[(d, TURN_KEYS[k])] = j
            else:  # 兜底：在 (s0, s0+3) 内搜索含该关键词的转向列
                found = None
                for jj in range(s0, min(s0 + 3, len(hdr_turn))):
                    if TURN_TOKENS[k] in _cell_str(hdr_turn[jj]):
                        found = jj
                        break
                if found is not None:
                    col_map[(d, TURN_KEYS[k])] = found
    return start_col, col_map


def _parse_flow_sheet(ws, range_by_period):
    """从流量 sheet 解析 flow。返回 (flow, notes)。"""
    rows = [_row(r) for r in ws.iter_rows(values_only=True)]
    blocks = _find_flow_blocks(rows)
    notes = []
    flow = []

    for bi, (L, period, label) in enumerate(blocks):
        nxt_L = blocks[bi + 1][0] if bi + 1 < len(blocks) else len(rows)
        start_col, col_map = _read_block_headers(rows, L)
        if not start_col:
            notes.append("时段 %s：找不到任何方向表头（东/西/北/南进口）" % period)
            continue

        bins = []
        zero_dir_seen = set()   # 用于提示：整块某进口无数据列
        k = L + 3
        # ---- 读 bin 行：col0 为时间即 bin ----
        while k < nxt_L:
            r = rows[k]
            t0 = _normalize_time(r[0])
            t1 = _normalize_time(r[1])
            if t0 is None or t1 is None:
                break
            movement = {}
            for d in DIRS:
                movement[d] = {}
                for tk in TURN_KEYS:
                    col = col_map.get((d, tk))
                    val = _to_int0(r[col]) if col is not None else 0
                    movement[d][tk] = val
            # 整方向全 0 标记（无数据列 / 空列）
            for d in DIRS:
                if movement[d] == {"left": 0, "straight": 0, "right": 0}:
                    zero_dir_seen.add(d)
            bins.append({
                "start": t0,
                "end": t1,
                "east": movement["east"],
                "west": movement["west"],
                "north": movement["north"],
                "south": movement["south"],
            })
            k += 1

        # ---- totals：优先取「配置参数/总流量」行（各进口分组首列）----
        totals = {d: None for d in DIRS}
        cfg_row_idx = None
        j = k
        while j < nxt_L:
            if _cell_str(rows[j][0]) == "配置参数":
                cfg_row_idx = j
                break
            j += 1
        if cfg_row_idx is not None:
            cfg = rows[cfg_row_idx]
            for d in DIRS:
                if d in start_col:
                    totals[d] = _to_int_or_none(cfg[start_col[d]])
        else:
            notes.append("时段 %s：未找到「配置参数/总流量」行，totals 由各 bin 求和回退" % period)

        # 兜底：total 为 None 但该方向有 bin 数据 -> bin 求和；仍无 -> 0
        for d in DIRS:
            if totals[d] is None:
                s = 0
                for b in bins:
                    for tk in TURN_KEYS:
                        s += b[d][tk]
                totals[d] = s

        # range：优先取配时表同名时段；否则由首个/末个 bin 推导
        rng = range_by_period.get(period)
        if not rng and bins:
            rng = "%s-%s" % (bins[0]["start"], bins[-1]["end"])
        if not rng:
            rng = DEFAULT_RANGE.get(period, "")

        for d in sorted(zero_dir_seen, key=lambda x: list(DIRS).index(x)):
            notes.append("时段 %s：%s 进口全表无流量数据(无列/空列)，bin 与 totals 记 0"
                         % (period, DIR_LABELS[d]))

        flow.append({"period": period, "range": rng, "bins": bins, "totals": totals})
    return flow, notes


# ---------------------------------------------------------------------------
# 3) 主流程：解析一个路口 xlsx -> 内存 dict
# ---------------------------------------------------------------------------
def parse_intersection(n):
    """解析 demo_<n> 的 xlsx，返回 JSON 对象；失败抛出异常。"""
    src_dir = os.path.join(SRC_ROOT, str(n), "路口数据")
    fname = "demo_%d流量和交叉口配时方案.xlsx" % n
    path = os.path.join(src_dir, fname)
    rel_source = "路口数据/%d/路口数据/%s" % (n, fname)   # JSON source 字段用正斜杠

    from openpyxl import load_workbook
    wb = load_workbook(path, read_only=True, data_only=True)
    try:
        sheet_names = list(wb.sheetnames)
        tsheet = _pick_timing_sheet(wb, sheet_names)
        if tsheet is None:
            raise ValueError("无法唯一确定配时 sheet；已打印全部 sheet 名")
        fsheet = next((s for s in sheet_names if "流量" in s), None)
        if fsheet is None:
            raise ValueError("找不到「流量」sheet，已打印全部 sheet 名：%s" % sheet_names)

        # ---- 配时表（信号配时数据 / 配信号时数据）----
        plans, plan_notes = _parse_plans(wb[tsheet])
        # 供流量 range 使用的 period->range
        range_by_period = {p["period"]: p["range"] for p in plans}

        # ---- 流量表 ----
        flow, flow_notes = _parse_flow_sheet(wb[fsheet], range_by_period)

        obj = {
            "demo": "demo_%d" % n,
            "source": rel_source,
            "plans": plans,
            "flow": flow,
        }
        return obj, plan_notes + flow_notes, tsheet, fsheet
    finally:
        wb.close()


# ---------------------------------------------------------------------------
# 4) 主程序
# ---------------------------------------------------------------------------
def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    log("=" * 78)
    log("解析雄安竞赛官方 20 个路口的配时方案 xlsx -> JSON")
    log("数据源目录 : %s" % SRC_ROOT)
    log("输出目录   : %s" % OUT_DIR)
    log("=" * 78)

    ok, failed = [], []
    all_notes = {}
    total_bytes = 0

    for n in range(1, 21):
        tag = "demo_%d" % n
        try:
            obj, notes, tsheet, fsheet = parse_intersection(n)
        except Exception as e:  # noqa: BLE001 —— 逐文件失败不应中断整批
            failed.append((tag, str(e)))
            log("\n[失败] %s：%s" % (tag, e))
            continue

        # 控制台概要：plans（period / 相位数 / 各相位 green / cycle）
        log("\n[%s] 配时 sheet=%r  流量 sheet=%r" % (tag, tsheet, fsheet))
        for p in obj["plans"]:
            greens = ", ".join(str(ph["green"]) for ph in p["phases"])
            log("    plans.%-4s %s  %d相  cycle=%-4s greens=[%s]"
                % (p["period"], p["range"], len(p["phases"]), p["cycle"], greens))
        for f in obj["flow"]:
            log("    flow.%-5s %s  bins=%d  totals=%s"
                % (f["period"], f["range"], len(f["bins"]),
                   {d: f["totals"][d] for d in DIRS}))
        if notes:
            all_notes[tag] = notes
            for nt in notes:
                log("    [注] %s" % nt)

        # 写 JSON（UTF-8，缩进 2，可读、幂等覆盖）
        out_path = os.path.join(OUT_DIR, "%s.json" % tag)
        with open(out_path, "w", encoding="utf-8") as fh:
            json.dump(obj, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
        sz = os.path.getsize(out_path)
        total_bytes += sz
        ok.append(tag)

    # ---- 汇总 ----
    log("\n" + "=" * 78)
    log("成功解析 %d / 20：%s" % (len(ok), ", ".join(ok)))
    if failed:
        log("失败 %d 个：" % len(failed))
        for tag, err in failed:
            log("    %s : %s" % (tag, err))
    if all_notes:
        log("全部提示/容错记录：")
        for tag, notes in all_notes.items():
            log("    [%s]" % tag)
            for nt in notes:
                log("        - %s" % nt)
    log("JSON 文件总大小：%.1f KB（%d bytes），输出于 %s"
        % (total_bytes / 1024.0, total_bytes, OUT_DIR))
    log("=" * 78)
    return 0 if len(ok) == 20 else 1


if __name__ == "__main__":
    sys.exit(main())
