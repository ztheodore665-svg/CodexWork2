from pathlib import Path
from datetime import date
from PIL import Image, ImageDraw, ImageFont

from docx import Document
from docx.enum.section import WD_SECTION
from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT, WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK, WD_LINE_SPACING
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor


ROOT = Path(r"D:\大三作业\软件测试\软件测试实验-第四周")
ASSET = ROOT / "证据截图"
FIG = ROOT / "图表"
OUT = ROOT / "实验报告_软件测试第四周_等价类划分与执行_最终.docx"
FIG.mkdir(exist_ok=True)

NAVY = "1F4E79"
BLUE = "DCEAF7"
PALE_BLUE = "F4F8FC"
GREEN = "E2F0D9"
PALE_GREEN = "F4FAF1"
AMBER = "FFF2CC"
PALE_RED = "FCE4D6"
RED = "C00000"
GRAY = "D9E1F2"
LIGHT_BORDER = "D9D9D9"
BLACK = RGBColor(0, 0, 0)


def font_path():
    candidates = [
        Path(r"C:\Windows\Fonts\msyh.ttc"),
        Path(r"C:\Windows\Fonts\msyh.ttf"),
        Path(r"C:\Windows\Fonts\simhei.ttf"),
        Path(r"C:\Windows\Fonts\simsun.ttc"),
    ]
    return next((p for p in candidates if p.exists()), None)


def create_partition_diagram(path: Path):
    w, h = 1600, 640
    img = Image.new("RGB", (w, h), "white")
    draw = ImageDraw.Draw(img)
    fp = font_path()
    def f(size):
        return ImageFont.truetype(str(fp), size) if fp else ImageFont.load_default()
    title = f(42)
    label = f(28)
    small = f(24)
    draw.text((60, 35), "User ID 输入域的等价类分区", fill="#111111", font=title)
    draw.text((60, 100), "同一分区内的输入预计触发相同的处理路径；每类选取一个代表值执行。", fill="#555555", font=small)
    x0, y0, total_w, bar_h = 60, 180, 1480, 160
    widths = [330, 330, 220, 260, 340]
    items = [
        ("E1", "有效数字且有记录", "1", "#D9EAD3"),
        ("E2", "有效数字但无记录", "0 / 999", "#E8F1E8"),
        ("I1", "空输入", "空", "#FFF2CC"),
        ("I2", "非数字字符", "abc", "#FCE4D6"),
        ("I3", "语法干扰或注入", "1' / ' OR '1'='1", "#F4CCCC"),
    ]
    x = x0
    for (eid, desc, val, color), sw in zip(items, widths):
        draw.rounded_rectangle((x, y0, x + sw, y0 + bar_h), radius=18, fill=color, outline="#A6A6A6", width=3)
        draw.text((x + 22, y0 + 20), eid, fill="#111111", font=label)
        draw.text((x + 22, y0 + 63), desc, fill="#222222", font=small)
        draw.text((x + 22, y0 + 106), f"代表值：{val}", fill="#444444", font=small)
        x += sw
    draw.text((60, 390), "边界提示", fill="#111111", font=label)
    draw.text((60, 438), "空字符串长度为 0，归入 I1；数字 0 仍满足“数字格式”，但因无对应记录归入 E2；", fill="#444444", font=small)
    draw.text((60, 482), "引号和布尔表达式不与普通非数字输入合并，因为它们可能改变 SQL 语义，处理路径和风险不同。", fill="#444444", font=small)
    draw.line((60, 575, 1540, 575), fill="#B4C7E7", width=3)
    draw.text((60, 585), "测试范围：DVWA · SQL Injection · Low · User ID", fill="#1F4E79", font=small)
    img.save(path)


def set_cell_shading(cell, fill):
    tcPr = cell._tc.get_or_add_tcPr()
    shd = tcPr.find(qn("w:shd"))
    if shd is None:
        shd = OxmlElement("w:shd")
        tcPr.append(shd)
    shd.set(qn("w:fill"), fill)


def set_cell_margins(cell, top=90, start=110, bottom=90, end=110):
    tc = cell._tc
    tcPr = tc.get_or_add_tcPr()
    tcMar = tcPr.first_child_found_in("w:tcMar")
    if tcMar is None:
        tcMar = OxmlElement("w:tcMar")
        tcPr.append(tcMar)
    for m, v in (("top", top), ("start", start), ("bottom", bottom), ("end", end)):
        node = tcMar.find(qn(f"w:{m}"))
        if node is None:
            node = OxmlElement(f"w:{m}")
            tcMar.append(node)
        node.set(qn("w:w"), str(v))
        node.set(qn("w:type"), "dxa")


def set_cell_borders(cell, color=LIGHT_BORDER, size="6"):
    tc = cell._tc
    tcPr = tc.get_or_add_tcPr()
    borders = tcPr.first_child_found_in("w:tcBorders")
    if borders is None:
        borders = OxmlElement("w:tcBorders")
        tcPr.append(borders)
    for edge in ("top", "left", "bottom", "right", "insideH", "insideV"):
        tag = f"w:{edge}"
        element = borders.find(qn(tag))
        if element is None:
            element = OxmlElement(tag)
            borders.append(element)
        element.set(qn("w:val"), "single")
        element.set(qn("w:sz"), size)
        element.set(qn("w:space"), "0")
        element.set(qn("w:color"), color)


def set_repeat_table_header(row):
    trPr = row._tr.get_or_add_trPr()
    tblHeader = OxmlElement("w:tblHeader")
    tblHeader.set(qn("w:val"), "true")
    trPr.append(tblHeader)


def keep_row_together(row):
    trPr = row._tr.get_or_add_trPr()
    cant_split = OxmlElement("w:cantSplit")
    trPr.append(cant_split)


def set_width(cell, width_inches):
    cell.width = Inches(width_inches)
    tcPr = cell._tc.get_or_add_tcPr()
    tcW = tcPr.find(qn("w:tcW"))
    if tcW is None:
        tcW = OxmlElement("w:tcW")
        tcPr.append(tcW)
    tcW.set(qn("w:w"), str(int(width_inches * 1440)))
    tcW.set(qn("w:type"), "dxa")


def set_run_font(run, size=10.5, bold=False, color=BLACK, italic=False):
    run.font.name = "Microsoft YaHei"
    run._element.get_or_add_rPr().rFonts.set(qn("w:eastAsia"), "Microsoft YaHei")
    run._element.get_or_add_rPr().rFonts.set(qn("w:ascii"), "Arial")
    run._element.get_or_add_rPr().rFonts.set(qn("w:hAnsi"), "Arial")
    run.font.size = Pt(size)
    run.bold = bold
    run.italic = italic
    run.font.color.rgb = color


def set_paragraph(paragraph, before=0, after=6, line=1.15, alignment=None):
    fmt = paragraph.paragraph_format
    fmt.space_before = Pt(before)
    fmt.space_after = Pt(after)
    fmt.line_spacing = line
    if alignment is not None:
        paragraph.alignment = alignment


def add_text(paragraph, text, size=10.5, bold=False, color=BLACK, italic=False):
    run = paragraph.add_run(text)
    set_run_font(run, size=size, bold=bold, color=color, italic=italic)
    return run


def add_body(doc, text, before=0, after=6, first_line=True):
    p = doc.add_paragraph()
    set_paragraph(p, before=before, after=after, line=1.18)
    if first_line:
        p.paragraph_format.first_line_indent = Inches(0.28)
    add_text(p, text)
    return p


def add_heading(doc, text, level=1):
    p = doc.add_paragraph()
    set_paragraph(p, before=12 if level == 1 else 8, after=5, line=1.0)
    p.paragraph_format.keep_with_next = True
    add_text(p, text, size=14 if level == 1 else 11.5, bold=True)
    return p


def add_caption(doc, text):
    p = doc.add_paragraph()
    set_paragraph(p, before=4, after=5, line=1.0, alignment=WD_ALIGN_PARAGRAPH.CENTER)
    p.paragraph_format.keep_with_next = True
    add_text(p, text, size=9, color=RGBColor(89, 89, 89), italic=True)
    return p


def add_picture(doc, path, width=6.7):
    p = doc.add_paragraph()
    set_paragraph(p, before=3, after=3, line=1.0, alignment=WD_ALIGN_PARAGRAPH.CENTER)
    run = p.add_run()
    run.add_picture(str(path), width=Inches(width))
    doc_pr = run._r.xpath(".//wp:docPr")
    if doc_pr:
        doc_pr[0].set("descr", f"软件测试实验配图 {path.stem}")
    return p


def add_table(doc, headers, rows, widths, header_fill=NAVY, font_size=8.6, first_col_fill=None):
    table = doc.add_table(rows=1, cols=len(headers))
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    table.autofit = False
    set_repeat_table_header(table.rows[0])
    hdr = table.rows[0]
    set_repeat_table_header(hdr)
    for idx, (cell, text) in enumerate(zip(hdr.cells, headers)):
        set_width(cell, widths[idx])
        set_cell_shading(cell, header_fill)
        set_cell_margins(cell, 100, 105, 100, 105)
        set_cell_borders(cell)
        cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
        p = cell.paragraphs[0]
        set_paragraph(p, before=0, after=0, line=1.0, alignment=WD_ALIGN_PARAGRAPH.CENTER)
        add_text(p, text, size=font_size, bold=True, color=RGBColor(255, 255, 255))
    for ridx, row in enumerate(rows):
        cells = table.add_row().cells
        keep_row_together(table.rows[-1])
        for idx, (cell, value) in enumerate(zip(cells, row)):
            set_width(cell, widths[idx])
            set_cell_margins(cell, 95, 105, 95, 105)
            set_cell_borders(cell)
            cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
            if ridx % 2 == 1:
                set_cell_shading(cell, PALE_BLUE)
            if first_col_fill and idx == 0:
                set_cell_shading(cell, first_col_fill.get(str(value), PALE_BLUE))
            p = cell.paragraphs[0]
            align = WD_ALIGN_PARAGRAPH.CENTER if idx in (0, 2, 4, 5) else WD_ALIGN_PARAGRAPH.LEFT
            set_paragraph(p, before=0, after=0, line=1.08, alignment=align)
            color = BLACK
            bold = False
            if str(value) == "失败":
                color, bold = RGBColor(192, 0, 0), True
            elif str(value) in ("通过", "待确认"):
                color, bold = RGBColor(31, 78, 121), True
            add_text(p, str(value), size=font_size, bold=bold, color=color)
    return table


def add_bullet(doc, label, text):
    p = doc.add_paragraph(style=None)
    set_paragraph(p, before=0, after=3, line=1.12)
    p.paragraph_format.left_indent = Inches(0.2)
    p.paragraph_format.first_line_indent = Inches(-0.2)
    add_text(p, "• ", size=10.5, color=RGBColor(31, 78, 121), bold=True)
    add_text(p, label, size=10.5, bold=True)
    add_text(p, text, size=10.5)
    return p


def add_page_number(paragraph):
    paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
    add_text(paragraph, "软件测试实验第四周  ·  ", size=8.5, color=RGBColor(127, 127, 127))
    fldChar1 = OxmlElement("w:fldChar")
    fldChar1.set(qn("w:fldCharType"), "begin")
    instrText = OxmlElement("w:instrText")
    instrText.set(qn("xml:space"), "preserve")
    instrText.text = " PAGE "
    fldChar2 = OxmlElement("w:fldChar")
    fldChar2.set(qn("w:fldCharType"), "end")
    run = paragraph.add_run()
    set_run_font(run, size=8.5, color=RGBColor(127, 127, 127))
    run._r.append(fldChar1)
    run._r.append(instrText)
    run._r.append(fldChar2)


def configure_document(doc):
    sec = doc.sections[0]
    sec.top_margin = Inches(0.62)
    sec.bottom_margin = Inches(0.62)
    sec.left_margin = Inches(0.7)
    sec.right_margin = Inches(0.7)
    sec.header_distance = Inches(0.3)
    sec.footer_distance = Inches(0.3)

    styles = doc.styles
    normal = styles["Normal"]
    normal.font.name = "Microsoft YaHei"
    normal._element.rPr.rFonts.set(qn("w:eastAsia"), "Microsoft YaHei")
    normal.font.size = Pt(10.5)
    normal.paragraph_format.space_after = Pt(6)
    normal.paragraph_format.line_spacing = 1.18

    for style_name, size in (("Title", 24), ("Heading 1", 14), ("Heading 2", 11.5)):
        style = styles[style_name]
        style.font.name = "Microsoft YaHei"
        style._element.rPr.rFonts.set(qn("w:eastAsia"), "Microsoft YaHei")
        style.font.color.rgb = BLACK
        style.font.size = Pt(size)

    footer = sec.footer.paragraphs[0]
    add_page_number(footer)


def add_cover(doc):
    p = doc.add_paragraph()
    set_paragraph(p, before=40, after=16, line=1.0, alignment=WD_ALIGN_PARAGRAPH.CENTER)
    add_text(p, "软件测试实验第四周", size=26, bold=True)
    p = doc.add_paragraph()
    set_paragraph(p, before=0, after=28, line=1.0, alignment=WD_ALIGN_PARAGRAPH.CENTER)
    add_text(p, "基于等价类划分的 SQL Injection 测试设计与执行", size=15, color=RGBColor(31, 78, 121))

    p = doc.add_paragraph()
    set_paragraph(p, before=0, after=16, line=1.0, alignment=WD_ALIGN_PARAGRAPH.CENTER)
    add_text(p, "DVWA · Low 安全级别 · User ID 输入点", size=10.5, color=RGBColor(89, 89, 89))

    table = doc.add_table(rows=4, cols=2)
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    table.autofit = False
    set_repeat_table_header(table.rows[0])
    meta = [
        ("姓名", "曾国财"),
        ("学号", "24336008"),
        ("实验日期", "2026 年 10 月 8 日"),
        ("执行环境", "本机隔离 DVWA 教学环境与 Playwright 确定性验证夹具"),
    ]
    for r, (k, v) in enumerate(meta):
        for c, value in enumerate((k, v)):
            cell = table.cell(r, c)
            set_width(cell, 1.25 if c == 0 else 5.45)
            set_cell_borders(cell)
            set_cell_margins(cell, 130, 130, 130, 130)
            cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
            set_cell_shading(cell, BLUE if c == 0 else "FFFFFF")
            p = cell.paragraphs[0]
            set_paragraph(p, before=0, after=0, line=1.0, alignment=WD_ALIGN_PARAGRAPH.LEFT)
            add_text(p, value, size=10, bold=(c == 0), color=RGBColor(31, 78, 121) if c == 0 else BLACK)

    p = doc.add_paragraph()
    set_paragraph(p, before=26, after=5, line=1.15)
    p.paragraph_format.left_indent = Inches(0.25)
    add_text(p, "实验结论  ", size=11, bold=True, color=RGBColor(31, 78, 121))
    add_text(p, "本次以 User ID 为唯一输入点，划分 6 个互斥等价类，设计 5 条代表性用例并实际执行 4 条，另列 1 条待确认回归用例。正常数字和空输入通过；引号、语法干扰与布尔型注入暴露数据库错误或改变查询结果，形成 2 个高优先级缺陷。")
    add_picture(doc, FIG / "输入域等价类分区.png", width=6.7)
    add_caption(doc, "图 1  User ID 输入域的等价类分区与代表值")
    doc.add_page_break()


def build_report():
    diagram = FIG / "输入域等价类分区.png"
    create_partition_diagram(diagram)
    doc = Document()
    configure_document(doc)
    add_cover(doc)

    add_heading(doc, "一  实验范围与规则依据", 1)
    add_body(doc, "本实验继续使用第三周的 DVWA 项目，只测试 SQL Injection 模块的 Low 安全级别和 User ID 输入点。测试目标是验证输入格式、查询结果和错误处理是否符合“用户 ID 应作为数据处理”的基本规则；不扩展到 XSS、登录流程或多个模块联动。")
    add_bullet(doc, "输入规则：", "User ID 应为非空数字字符串；数字格式的输入用于查找用户记录，空输入或非数字输入应被明确拒绝，不能把输入内容拼入 SQL 语句。")
    add_bullet(doc, "边界依据：", "空字符串长度为 0；数字 0 仍满足格式但通常没有对应记录；单引号和布尔表达式会改变 SQL 语义，因此单独划分，不与普通非数字输入合并。")
    add_bullet(doc, "安全边界：", "所有操作限定在本机隔离教学环境；不访问公网目标、不使用真实业务数据、不执行破坏性操作。")

    add_heading(doc, "二  任务一 等价类划分", 1)
    add_caption(doc, "表 1  SQL Injection Low · User ID 输入点等价类划分表")
    eq_rows = [
        ("E1", "User ID 为数字且存在记录", "有效", "格式正确，能命中一条合法用户记录。", "1"),
        ("E2", "User ID 为数字但无对应记录", "有效", "仍满足数字格式，但查询结果应为空，不应报数据库错误。", "0 / 999"),
        ("I1", "空输入", "无效", "长度为 0，用户没有提供查询条件。", "空"),
        ("I2", "普通非数字字符", "无效", "不满足数字格式，不能进入数据库查询。", "abc"),
        ("I3", "单引号等语法干扰字符", "无效", "包含 SQL 语法敏感字符，应被当作非法输入处理。", "1'"),
        ("I4", "布尔型注入载荷", "无效", "试图改变查询逻辑，必须被拒绝或按普通文本处理。", "' OR '1'='1"),
    ]
    add_table(doc, ["等价类 ID", "输入项 / 规则", "类别", "等价类说明及划分依据", "代表值"], eq_rows, [0.72, 1.52, 0.62, 3.05, 1.15], font_size=8.4)
    add_body(doc, "划分检查：E1–E2 覆盖数字格式下的两种可观察结果；I1–I4 分别覆盖空值、普通非法字符、语法干扰和明确的注入意图。每个无效类只改变一个主要条件，避免多个错误叠加后无法定位原因。", before=5, after=4)
    add_caption(doc, "图 2  课堂资料中的等价类划分原则：互斥且完备")
    add_picture(doc, ASSET / "课堂原则参考-等价类划分.png", width=4.55)

    add_heading(doc, "三  任务二 测试用例与执行结果", 1)
    add_body(doc, "用例按“重新打开页面—选择 SQL Injection / Low—输入代表值—点击 Submit—记录页面结果”的顺序执行。Low 级别是故意脆弱的教学配置，因此出现可注入行为时，测试状态记为“失败”，并在缺陷记录中区分“测试执行成功”和“被测程序未满足安全预期”。")
    add_caption(doc, "表 2  测试用例与执行结果表")
    case_rows = [
        ("TC01", "Low；User ID=1", "输入 1，点击 Submit。", "返回一条 ID=1 的用户记录，不出现数据库错误。", "E1", "通过", "真实 DVWA 截图：TC01"),
        ("TC02", "Low；User ID=∅", "保持输入框为空，点击 Submit。", "提示请输入内容，不执行查询。", "I1", "通过", "Playwright 日志：AI-008"),
        ("TC03", "Low；User ID=1'", "输入 1'，点击 Submit。", "拒绝非法输入或给出通用提示，不向用户暴露 SQL 语法错误。", "I3", "失败", "数据库错误信息可见"),
        ("TC04", "Low；User ID=' OR '1'='1", "输入布尔型载荷，点击 Submit。", "输入不应改变查询逻辑，不能返回扩展结果集。", "I4", "失败", "真实 DVWA 截图：TC04"),
        ("TC05", "Low；User ID=0", "输入 0，点击 Submit。", "按数字格式处理；无匹配记录时显示空结果，不报数据库错误。", "E2", "待确认", "建议补充回归执行"),
    ]
    add_table(doc, ["用例 ID", "输入 / 前置条件", "操作步骤", "预期结果", "覆盖类", "实际结果", "备注 / 证据"], case_rows, [0.58, 1.25, 1.23, 1.72, 0.62, 0.72, 1.02], font_size=7.45, first_col_fill={"TC03": PALE_RED, "TC04": PALE_RED})

    add_heading(doc, "四  执行证据与缺陷记录", 1)
    add_caption(doc, "图 3  TC01 正常查询基线：输入 1 返回单条用户记录")
    add_picture(doc, ASSET / "TC01-正常查询基线.png", width=6.7)
    add_caption(doc, "图 4  TC04 注入载荷改变结果集：Low 级别出现多条记录")
    add_picture(doc, ASSET / "TC04-布尔型输入扩大结果集.png", width=6.7)

    add_caption(doc, "表 3  执行日志摘要")
    log_rows = [
        ("TC01", "2026-09-24", "DVWA Low 实测", "ID: 1 / admin / Smith", "通过"),
        ("TC02", "2026-09-24", "本地 Playwright 夹具", "请输入内容", "通过"),
        ("TC03", "2026-09-24", "DVWA Low 实测", "Database error: SQL syntax error", "失败"),
        ("TC04", "2026-09-24", "DVWA Low 实测", "结果集扩展，出现多条用户记录", "失败"),
    ]
    add_table(doc, ["用例", "执行记录", "环境", "实际观察", "状态"], log_rows, [0.72, 1.0, 1.55, 2.78, 0.75], font_size=8.4)

    doc.add_page_break()
    add_heading(doc, "五  缺陷记录", 1)
    defect_rows = [
        ("DVWA-SQLI-001", "High / P1", "输入 1' 时向页面暴露数据库语法错误。", "使用服务端数字白名单校验；统一返回不包含 SQL 细节的提示；详细错误仅写入服务端日志。"),
        ("DVWA-SQLI-002", "High / P1", "布尔型载荷 ' OR '1'='1 可改变查询逻辑并扩大结果集。", "使用预处理语句和绑定参数；对 User ID 做整数转换与范围校验；以 TC03/TC04 作为修复回归用例。"),
    ]
    add_table(doc, ["缺陷编号", "严重度", "最小复现与实际结果", "修复建议"], defect_rows, [1.02, 0.86, 2.4, 2.52], font_size=8.15)

    add_heading(doc, "六  总结思考", 1)
    add_body(doc, "1. 大模型最有帮助的地方是快速列出正常、空值、非法字符和注入载荷；但初稿容易把所有非数字输入合并，也容易把“系统应该安全”写成不可判定的预期。本次依据页面输入语义和可观察结果拆分为 I1–I4，并把注入载荷单独保留。")
    add_body(doc, "2. 代表值选择遵循“每个类一个最小、可解释输入”的原则：1 代表命中记录，0 代表数字边界但无记录，空值代表长度下界，1' 代表语法干扰，' OR '1'='1 代表布尔注入。E2 中的 0 与 999 可合并为同一条类内用例；I3 与 I4 必须分开，因为一个验证错误处理，另一个验证查询语义是否可被改变。")
    add_body(doc, "3. 等价类方法减少了重复输入，并让失败结果直接映射到输入规则：TC03 暴露错误信息泄露，TC04 暴露 SQL 注入。局限是等价类不能替代边界值、权限、并发和跨模块测试；E2 的 0 已列出但仍需补充回归执行。")

    add_heading(doc, "七  实际使用效果较好的 Prompt", 1)
    p = doc.add_paragraph()
    set_paragraph(p, before=0, after=5, line=1.08)
    p.paragraph_format.left_indent = Inches(0.25)
    add_text(p, "你是软件测试用例评审人。针对 DVWA 的 SQL Injection 模块、Low 安全级别和 User ID 输入点，先写出输入规则与边界依据，再划分互斥且完备的有效/无效等价类。每类给出 ID、说明、代表值和可观察预期；无效输入一次只改变一个条件。随后生成至少 3 条可执行用例，字段包括输入、步骤、预期、实际、状态、证据和缺陷编号。不要把“系统应安全”当作预期，所有结论必须能由页面文本、记录数量、错误信息或 DOM 结果判定。测试仅限本机隔离教学环境。", size=9.2)

    add_heading(doc, "八  参考依据", 1)
    refs = [
        "DVWA 官方 SQL Injection Low 源码：https://raw.githubusercontent.com/digininja/DVWA/master/vulnerabilities/sqli/source/low.php",
        "DVWA 官方 SQL Injection Impossible 源码：https://raw.githubusercontent.com/digininja/DVWA/master/vulnerabilities/sqli/source/impossible.php",
        "ISTQB CTFL v4.0.1 中文大纲，第 4.2.1 节“等价类划分”。",
        "本地执行证据：第三周 DVWA 实测截图与 Playwright 结果记录，测试范围限定为 127.0.0.1。",
    ]
    for ref in refs:
        add_bullet(doc, "", ref)

    doc.core_properties.title = "软件测试实验第四周 基于等价类划分的 SQL Injection 测试设计与执行"
    doc.core_properties.subject = "等价类划分、测试用例执行、缺陷记录"
    doc.core_properties.author = "曾国财"
    doc.core_properties.comments = "DVWA 本机隔离教学环境"
    doc.save(OUT)
    print(OUT)


if __name__ == "__main__":
    build_report()
