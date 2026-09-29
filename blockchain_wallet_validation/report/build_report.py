from __future__ import annotations

import os
import subprocess
import tempfile
from datetime import date
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    Flowable,
    HRFlowable,
    PageBreak,
    Paragraph,
    Preformatted,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)
from reportlab.graphics.shapes import Drawing, Line, Rect, String


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "output" / "experiment_report.pdf"
FONT_PATH = Path(r"C:\Windows\Fonts\simhei.ttf")
# simhei.ttf contains the full Chinese glyph set in this environment; reuse it
# for the bold face to avoid missing-glyph boxes in section headings.
BOLD_FONT_PATH = Path(r"C:\Windows\Fonts\simhei.ttf")


def register_fonts() -> tuple[str, str]:
    regular, bold = "SimSun", "SimSun-Bold"
    pdfmetrics.registerFont(TTFont(regular, str(FONT_PATH)))
    pdfmetrics.registerFont(TTFont(bold, str(BOLD_FONT_PATH)))
    return regular, bold


def run_command(args: list[str]) -> str:
    env = os.environ.copy()
    if not env.get("GOCACHE"):
        env["GOCACHE"] = tempfile.mkdtemp(prefix="wallet-go-build-")
    completed = subprocess.run(
        args,
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    output = (completed.stdout + completed.stderr).strip()
    return f"$ {' '.join(args)}\n{output}\n[exit code: {completed.returncode}]"


def p(text: str, style: ParagraphStyle) -> Paragraph:
    return Paragraph(text, style)


class VerificationFlow(Flowable):
    def __init__(self, regular_font: str):
        super().__init__()
        self.width = 170 * mm
        self.height = 55 * mm
        self.font = regular_font

    def draw(self) -> None:
        canvas = self.canv
        boxes = [
            (4, 38, 34, 12, "Alice\nNewWallet()", colors.HexColor("#E6F0FF")),
            (49, 38, 34, 12, "TransferData\n{To, Amount}", colors.HexColor("#EAF7EE")),
            (94, 38, 34, 12, "Sign(PrivateKey,\nMessage())", colors.HexColor("#FFF2DA")),
            (139, 38, 27, 12, "sig", colors.HexColor("#F6E8FF")),
            (72, 9, 55, 14, "Verify(PublicKey, Message(), sig)\n共识节点：true / false", colors.HexColor("#E9F3F2")),
        ]
        for x, y, w, h, label, fill in boxes:
            x0, y0 = x * mm, y * mm
            canvas.setFillColor(fill)
            canvas.setStrokeColor(colors.HexColor("#477A78"))
            canvas.roundRect(x0, y0, w * mm, h * mm, 2 * mm, fill=1, stroke=1)
            lines = label.split("\n")
            canvas.setFillColor(colors.HexColor("#173B3B"))
            canvas.setFont(self.font, 8.2)
            for i, line in enumerate(lines):
                canvas.drawCentredString(x0 + w * mm / 2, y0 + h * mm - (i + 1) * 4.5 * mm, line)
        canvas.setStrokeColor(colors.HexColor("#477A78"))
        canvas.setLineWidth(1.2)
        arrows = [
            (38, 44, 49, 44),
            (83, 44, 94, 44),
            (128, 44, 139, 44),
            (155, 38, 125, 23),
            (94, 38, 94, 23),
            (80, 38, 94, 23),
        ]
        for x1, y1, x2, y2 in arrows:
            canvas.line(x1 * mm, y1 * mm, x2 * mm, y2 * mm)
            dx, dy = x2 - x1, y2 - y1
            length = max((dx * dx + dy * dy) ** 0.5, 1)
            ux, uy = dx / length, dy / length
            px, py = -uy, ux
            tipx, tipy = x2 * mm, y2 * mm
            size = 2.2 * mm
            canvas.setFillColor(colors.HexColor("#477A78"))
            canvas.saveState()
            path = canvas.beginPath()
            path.moveTo(tipx, tipy)
            path.lineTo(tipx - ux * size + px * size * 0.55, tipy - uy * size + py * size * 0.55)
            path.lineTo(tipx - ux * size - px * size * 0.55, tipy - uy * size - py * size * 0.55)
            path.close()
            canvas.drawPath(path, fill=1, stroke=0)
            canvas.restoreState()


def footer(canvas, document, regular_font: str) -> None:
    canvas.saveState()
    canvas.setStrokeColor(colors.HexColor("#D7E3E1"))
    canvas.line(18 * mm, 14 * mm, 192 * mm, 14 * mm)
    canvas.setFont(regular_font, 8)
    canvas.setFillColor(colors.HexColor("#60706F"))
    canvas.drawString(18 * mm, 9 * mm, "区块链原理与技术实验 · 第四次实验")
    canvas.drawRightString(192 * mm, 9 * mm, f"第 {document.page} 页")
    canvas.restoreState()


def build() -> Path:
    regular, bold = register_fonts()
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)

    styles = getSampleStyleSheet()
    title = ParagraphStyle(
        "TitleCN", parent=styles["Title"], fontName=bold, fontSize=24, leading=32,
        alignment=TA_CENTER, textColor=colors.HexColor("#163B3B"), spaceAfter=11 * mm,
    )
    subtitle = ParagraphStyle(
        "SubtitleCN", parent=styles["Normal"], fontName=regular, fontSize=12, leading=20,
        alignment=TA_CENTER, textColor=colors.HexColor("#4F6867"),
    )
    h1 = ParagraphStyle(
        "H1CN", parent=styles["Heading1"], fontName=bold, fontSize=16, leading=23,
        textColor=colors.HexColor("#1B5D5A"), spaceBefore=5 * mm, spaceAfter=3 * mm,
    )
    h2 = ParagraphStyle(
        "H2CN", parent=styles["Heading2"], fontName=bold, fontSize=12, leading=18,
        textColor=colors.HexColor("#2E6F6B"), spaceBefore=3 * mm, spaceAfter=2 * mm,
    )
    body = ParagraphStyle(
        "BodyCN", parent=styles["BodyText"], fontName=regular, fontSize=10.5, leading=18,
        textColor=colors.HexColor("#243130"), firstLineIndent=0,
        spaceAfter=2.3 * mm, alignment=TA_LEFT,
    )
    small = ParagraphStyle(
        "SmallCN", parent=body, fontSize=8.8, leading=14, textColor=colors.HexColor("#52615F"),
    )
    code_style = ParagraphStyle(
        "CodeCN", parent=styles["Code"], fontName="Courier", fontSize=7.2, leading=10,
        textColor=colors.HexColor("#193C3B"), leftIndent=4 * mm, rightIndent=4 * mm,
    )
    caption = ParagraphStyle(
        "CaptionCN", parent=small, alignment=TA_CENTER, textColor=colors.HexColor("#60706F"),
        spaceBefore=1 * mm, spaceAfter=2 * mm,
    )

    story: list[Flowable] = []
    story += [Spacer(1, 20 * mm), p("第 4 次实验课作业", title)]
    story += [p("对 BTC 所有权的确认：钱包地址、数字签名与共识节点验签", subtitle)]
    story += [Spacer(1, 19 * mm), HRFlowable(width="88%", thickness=1.5, color=colors.HexColor("#4B837E"))]
    story += [Spacer(1, 12 * mm)]
    info = [
        [p("课程", body), p("区块链原理与技术实验", body)],
        [p("学号 / 姓名", body), p("24336008 / 曾国财", body)],
        [p("完成日期", body), p(str(date(2026, 9, 29)), body)],
        [p("实验依据", body), p("实验课 PDF 第 42-43 页", body)],
    ]
    info_table = Table(info, colWidths=[38 * mm, 120 * mm], hAlign="CENTER")
    info_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#EAF4F1")),
        ("BOX", (0, 0), (-1, -1), 0.7, colors.HexColor("#8BAFAC")),
        ("INNERGRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#C9DAD7")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 4 * mm),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4 * mm),
        ("TOPPADDING", (0, 0), (-1, -1), 3 * mm),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3 * mm),
    ]))
    story += [info_table, Spacer(1, 22 * mm), p("关键词：secp256k1 · Base58Check · ECDSA · 数字签名 · 所有权验证", subtitle), PageBreak()]

    story += [p("一、实验目的与任务分解", h1)]
    story += [p("本实验根据题目要求，模拟 Alice 向 Bob 发起转账以及共识节点确认所有权的过程。核心判断是：只有持有 Alice 私钥的一方，才能生成可被 Alice 公钥验证通过的签名。转账金额和收款地址都被编码进待签名消息，因此消息被修改后，原签名立即失效。", body)]
    task_data = [
        [p("题目步骤", body), p("实现内容", body), p("对应代码", body)],
        [p("1. 生成两个钱包", body), p("分别创建 Alice、Bob 的 secp256k1 密钥对和地址", body), p("NewWallet()", code_style)],
        [p("2. 构造交易", body), p("使用 Bob 地址作为 To，金额以聪为单位保存", body), p("TransferData{To, Amount}", code_style)],
        [p("3. Alice 签名", body), p("对规范化交易消息做 SHA-256 后生成 DER 签名", body), p("Sign(privateKey, Message())", code_style)],
        [p("4. 节点验签", body), p("用 Alice 公钥验证同一消息和签名的对应关系", body), p("Verify(publicKey, Message(), sig)", code_style)],
    ]
    task_table = Table(task_data, colWidths=[27 * mm, 91 * mm, 50 * mm], repeatRows=1)
    task_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2F7772")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("BOX", (0, 0), (-1, -1), 0.65, colors.HexColor("#7DA9A4")),
        ("INNERGRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#C9DAD7")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 3 * mm),
        ("RIGHTPADDING", (0, 0), (-1, -1), 3 * mm),
        ("TOPPADDING", (0, 0), (-1, -1), 2.6 * mm),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2.6 * mm),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F5FAF8")]),
    ]))
    story += [task_table, p("表 1  实验要求与代码实现的对应关系", caption)]

    story += [p("二、实验原理", h1)]
    story += [p("钱包由私钥、公钥和地址组成。私钥是签名能力的根源；公钥由私钥推导得到，可公开给共识节点；地址是公钥哈希的可读编码。代码用压缩公钥执行 SHA-256 + RIPEMD-160，添加版本字节和双 SHA-256 校验和后进行 Base58 编码。", body)]
    story += [p("数字签名的验证逻辑", h2)]
    story += [p("Alice 先把收款地址和金额编码成唯一消息，再用私钥签名。共识节点拿到转出公钥、原始消息和签名后，重新计算消息摘要并执行 ECDSA 验证。签名验证通过只能说明消息由对应私钥持有者签出；地址校验则只检查地址格式和校验和，不能单独证明资产所有权。", body)]
    story += [VerificationFlow(regular), p("图 1  Alice 构造交易与共识节点验签流程", caption), PageBreak()]

    story += [p("三、系统设计与关键实现", h1)]
    story += [p("项目使用 Go 标准工程结构，密码学曲线使用题目示意图中的 secp256k1。第三方库只负责成熟的曲线和签名运算；地址的 Base58Check 编解码在本项目内实现，便于观察地址校验过程。", body)]
    api_data = [
        [p("接口", body), p("作用", body), p("安全要点", body)],
        [p("NewWallet()", code_style), p("生成随机私钥、公钥和地址", body), p("私钥只保存在 Wallet 内，不打印、不进入交易消息", body)],
        [p("TransferData.Message()", code_style), p("生成带版本前缀的规范化消息", body), p("To 与 Amount 均绑定在签名摘要中", body)],
        [p("Sign()", code_style), p("SHA-256 + secp256k1 ECDSA，输出 DER 签名", body), p("只有私钥持有者能生成有效签名", body)],
        [p("Verify()", code_style), p("解析签名并用公钥验证", body), p("错误签名返回 false 或明确错误", body)],
        [p("ValidateAddress()", code_style), p("校验 Base58 字符、版本、长度和 checksum", body), p("防止地址输入错误，但不替代验签", body)],
    ]
    api_table = Table(api_data, colWidths=[43 * mm, 65 * mm, 60 * mm], repeatRows=1)
    api_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2F7772")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("BOX", (0, 0), (-1, -1), 0.65, colors.HexColor("#7DA9A4")),
        ("INNERGRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#C9DAD7")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 3 * mm),
        ("RIGHTPADDING", (0, 0), (-1, -1), 3 * mm),
        ("TOPPADDING", (0, 0), (-1, -1), 2.5 * mm),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5 * mm),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F5FAF8")]),
    ]))
    story += [api_table, p("表 2  核心接口设计", caption)]
    story += [p("关键代码（wallet.go）", h2)]
    code = """transfer := TransferData{To: bob.Address, Amount: 50000000}\nif err := transfer.Validate(); err != nil {\n    log.Fatal(err)\n}\nsig, err := Sign(alice.PrivateKey, transfer.Message())\nif err != nil {\n    log.Fatal(err)\n}\nok, err := Verify(alice.PublicKey, transfer.Message(), sig)\nfmt.Printf(\"correct signature: ok=%t err=%v\\n\", ok, err)"""
    story += [Preformatted(code, code_style), p("代码清单及完整实现位于随报告提交的 wallet_validation 目录。", small)]

    story += [p("四、实验过程与结果", h1)]
    story += [p("在 Go 1.26.2 环境中执行单元测试和演示程序。测试覆盖正确签名、篡改金额、错误公钥、非法 DER 签名和地址校验和变异五种情况。", body)]
    test_output = run_command(["go", "test", "-v"])
    demo_output = run_command(["go", "run", "."])
    story += [p("4.1 单元测试输出", h2), Preformatted(test_output, code_style)]
    story += [p("4.2 演示程序输出", h2), Preformatted(demo_output, code_style)]
    story += [p("结果判断：4 个单元测试全部通过；正确签名返回 ok=true；将金额从 50000000 修改为 50000001 后返回 ok=false；改用 Bob 公钥验证 Alice 的签名同样返回 ok=false。由此可确认签名同时绑定了签名者和交易消息。", body)]

    story += [PageBreak(), p("五、结果分析与安全性讨论", h1)]
    analysis_data = [
        [p("验证场景", body), p("预期", body), p("实际", body), p("结论", body)],
        [p("Alice 公钥 + 原始消息 + Alice 签名", body), p("通过", body), p("ok=true", code_style), p("合法所有权证明", body)],
        [p("Alice 公钥 + 篡改金额 + 原签名", body), p("拒绝", body), p("ok=false", code_style), p("签名绑定交易内容", body)],
        [p("Bob 公钥 + 原始消息 + Alice 签名", body), p("拒绝", body), p("ok=false", code_style), p("签名绑定私钥持有者", body)],
        [p("地址字符或 checksum 被修改", body), p("拒绝", body), p("返回错误", code_style), p("输入地址可检测", body)],
    ]
    analysis_table = Table(analysis_data, colWidths=[55 * mm, 26 * mm, 34 * mm, 53 * mm], repeatRows=1)
    analysis_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2F7772")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("BOX", (0, 0), (-1, -1), 0.65, colors.HexColor("#7DA9A4")),
        ("INNERGRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#C9DAD7")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 2.5 * mm),
        ("RIGHTPADDING", (0, 0), (-1, -1), 2.5 * mm),
        ("TOPPADDING", (0, 0), (-1, -1), 2.5 * mm),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5 * mm),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F5FAF8")]),
    ]))
    story += [analysis_table, p("表 3  验证结果对照", caption)]
    story += [p("本实验是交易所有权确认的最小模型，仍有三点需要在真实区块链系统中补充：第一，验证 UTXO 是否存在且未被花费；第二，检查 Alice 是否有足够余额；第三，使用交易 ID、输入引用、时间锁和区块确认数等机制防止重放与双花。本实验聚焦的是题目要求的“私钥控制权验证”，不模拟完整账本状态。", body)]
    story += [p("六、实验总结", h1)]
    story += [p("本实验完成了从钱包生成、地址构造、交易消息规范化、Alice 私钥签名到共识节点公钥验签的完整闭环。实验结果说明：地址主要用于表示收款目标，真正证明转出权的是与交易消息绑定的数字签名；任何对金额、收款地址或签名者的替换都会使验证失败。实现代码可直接用 `go test -v` 和 `go run .` 复现。", body)]
    doc = SimpleDocTemplate(
        str(OUTPUT), pagesize=A4, rightMargin=18 * mm, leftMargin=18 * mm,
        topMargin=16 * mm, bottomMargin=20 * mm, title="第4次实验课作业：对 BTC 所有权的确认",
        author="24336008 曾国财",
    )
    doc.build(story, onFirstPage=lambda c, d: footer(c, d, regular), onLaterPages=lambda c, d: footer(c, d, regular))
    return OUTPUT


if __name__ == "__main__":
    print(build())
