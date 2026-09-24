from pathlib import Path
import re
import shutil
import zipfile

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    Image,
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)


BASE = Path(r"D:\大三作业\全国大学生数智链应用大赛\2026 NCSC AttackPilot 中山大学")
OUT = BASE / "竞赛提交材料草案"
PACKAGE = OUT / "中山大学-待填写队长-AttackPilot"
SOURCE = BASE / "源代码"
MEDIA = BASE.parent / ".review_assets"
STAGING = OUT / ".source_staging"

FONT = r"C:\Windows\Fonts\simhei.ttf"
FONT_BOLD = r"C:\Windows\Fonts\simhei.ttf"
pdfmetrics.registerFont(TTFont("SimHei", FONT))
pdfmetrics.registerFont(TTFont("SimHeiBold", FONT_BOLD))

styles = getSampleStyleSheet()
styles.add(ParagraphStyle(
    name="CNTitle", parent=styles["Title"], fontName="SimHeiBold", fontSize=22,
    leading=30, alignment=TA_CENTER, textColor=colors.HexColor("#152238"), spaceAfter=10,
))
styles.add(ParagraphStyle(
    name="CNSubtitle", parent=styles["Normal"], fontName="SimHei", fontSize=11,
    leading=18, alignment=TA_CENTER, textColor=colors.HexColor("#526173"), spaceAfter=18,
))
styles.add(ParagraphStyle(
    name="CNH1", parent=styles["Heading1"], fontName="SimHeiBold", fontSize=15,
    leading=23, textColor=colors.HexColor("#152238"), spaceBefore=12, spaceAfter=7,
))
styles.add(ParagraphStyle(
    name="CNH2", parent=styles["Heading2"], fontName="SimHeiBold", fontSize=11.5,
    leading=18, textColor=colors.HexColor("#1E4E79"), spaceBefore=7, spaceAfter=4,
))
styles.add(ParagraphStyle(
    name="CNBody", parent=styles["BodyText"], fontName="SimHei", fontSize=9.4,
    leading=16, alignment=TA_LEFT, textColor=colors.HexColor("#222222"), spaceAfter=5,
    wordWrap="CJK",
))
styles.add(ParagraphStyle(
    name="CNSmall", parent=styles["BodyText"], fontName="SimHei", fontSize=8.3,
    leading=13, textColor=colors.HexColor("#333333"), wordWrap="CJK",
))
styles.add(ParagraphStyle(
    name="CNCaption", parent=styles["BodyText"], fontName="SimHei", fontSize=8.2,
    leading=12, alignment=TA_CENTER, textColor=colors.HexColor("#64748B"), spaceAfter=8,
))


def P(text, style="CNBody"):
    return Paragraph(text.replace("\n", "<br/>").replace("&", "&amp;"), styles[style])


def header_footer(canvas, doc):
    canvas.saveState()
    canvas.setStrokeColor(colors.HexColor("#D9E2EC"))
    canvas.line(18 * mm, 14 * mm, 192 * mm, 14 * mm)
    canvas.setFont("SimHei", 7.5)
    canvas.setFillColor(colors.HexColor("#64748B"))
    canvas.drawString(18 * mm, 9 * mm, "AttackPilot 竞赛提交材料草案")
    canvas.drawRightString(192 * mm, 9 * mm, f"{doc.page}")
    canvas.restoreState()


def table(data, widths, header=True, small=False):
    converted = []
    for r, row in enumerate(data):
        converted.append([P(str(c), "CNSmall" if small else "CNBody") for c in row])
    t = Table(converted, colWidths=widths, repeatRows=1 if header else 0, hAlign="LEFT")
    commands = [
        ("GRID", (0, 0), (-1, -1), 0.45, colors.HexColor("#C9D4DF")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]
    if header:
        commands += [
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1E4E79")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ]
        # Header paragraphs use the registered font and need explicit white text.
        for cell in converted[0]:
            for frag in cell.frags:
                frag.fontName = "SimHeiBold"
                frag.textColor = colors.white
    for r in range(1 if header else 0, len(data)):
        if r % 2 == 0:
            commands.append(("BACKGROUND", (0, r), (-1, r), colors.HexColor("#F5F8FB")))
    t.setStyle(TableStyle(commands))
    return t


def add_image(story, path, width, caption):
    if not path.exists():
        return
    from PIL import Image as PILImage
    with PILImage.open(path) as im:
        w, h = im.size
    height = width * h / w
    story.append(Image(str(path), width=width, height=height))
    story.append(P(caption, "CNCaption"))


def build_design_pdf(path):
    story = [
        Spacer(1, 25 * mm),
        P("AttackPilot 设计说明书", "CNTitle"),
        P("2026 年第三届全国大学生数智链应用大赛\n区块链大类 技术应用—应用实践与应用设计", "CNSubtitle"),
        table([
            ["作品名称", "AttackPilot"],
            ["所选分类", "技术应用—应用实践与应用设计"],
            ["学校名称", "中山大学"],
            ["团队队长", "待填写"],
            ["团队成员", "待填写"],
            ["指导教师", "待填写"],
            ["提交日期", "2026 年待填写"],
        ], [35 * mm, 125 * mm], header=False),
        Spacer(1, 8 * mm),
        P("本说明书介绍 AttackPilot 的应用背景、设计理念、开发过程、区块链技术应用、作品特色与不足，并给出可复核的部署和效果材料。", "CNBody"),
        PageBreak(),
        P("1. 作品概述", "CNH1"),
        P("1.1 创作背景", "CNH2"),
        P("区块链交易和智能合约执行过程公开可查，但真实安全事件往往由准备、触发、套利和清理等多笔交易共同组成。安全分析人员需要在区块浏览器、Trace 工具、合约源码、模拟环境和日志之间反复切换，才能回答资金如何被转移、漏洞位于哪段逻辑以及修复是否有效。AttackPilot 面向这一具体痛点，将交易收据、事件日志、资金变化、执行 Trace 和合约源码统一为可追溯的证据链，并以多智能体协作推进分析和验证，降低链上攻击复盘的时间成本和理解门槛。", "CNBody"),
        P("1.2 作品简介", "CNH2"),
        P("AttackPilot 是面向链上安全事件的攻击复盘 Web 应用。用户可以输入单笔或多笔交易 Hash，也可以选择 SushiSwap、ApeCoin 等缓存案例。系统依次完成链上取证、攻击检测、跨交易上下文分析、动态 Trace 探索、根因定位、补丁生成和补丁重放验证，并将关键证据、Agent 状态、任务日志、攻击路径和结构化报告集中展示。平台还提供漏洞教学、案例知识库、报告导出和取证助手，面向安全分析人员、合约开发者和学习研究人员使用。", "CNBody"),
        P("1.3 目标受众与使用场景", "CNH2"),
        P("安全分析人员用于事故初筛、攻击路径复盘和证据核验；合约开发者用于定位根因、查看修复建议并检查补丁能否阻断原攻击；学习研究人员用于通过真实案例理解漏洞机制、链上信号和修复思路。", "CNBody"),
        P("2. 设计理念", "CNH1"),
        P("2.1 设计主题与创意来源", "CNH2"),
        P("作品以“证据驱动的链上攻击复盘”为主题，方法基础来自团队 TracePilot 研究工作。创意来源于安全专家在实际复盘中不断提出假设、查找证据、运行验证并修正判断的工作方式。平台把这一闭环过程显式化，使模型推理不脱离交易事实和执行结果。", "CNBody"),
        P("2.2 核心设计思路", "CNH2"),
        P("系统按“交易取证—智能体分析—补丁验证—证据归档”组织工作流。宏观分析智能体识别交易关系、地址角色和资金流向；Trace 智能体围绕可疑节点选择性展开；补丁智能体生成修复建议并在模拟环境中重放攻击；审查智能体检查证据链和结果一致性。完成的报告进入知识库，为后续分析和教学提供可复用上下文。", "CNBody"),
        P("2.3 作品主要功能", "CNH2"),
        table([
            ["功能模块", "主要能力"],
            ["交易复盘", "输入单笔/多笔交易 Hash，选择真实缓存案例，执行链上取证。"],
            ["攻击检测与定位", "识别攻击角色、资金流、攻击阶段，并将根因定位到函数和业务规则。"],
            ["补丁生成与验证", "生成修复建议，编译并重放原始攻击，反馈验证结果。"],
            ["结果复核与交互", "展示 Agent 状态、证据、攻击路径、日志、报告和取证助手。"],
            ["学习与知识沉淀", "整理真实案例的背景、PoC、链上信号和修复思路，支持检索。"],
        ], [42 * mm, 118 * mm], small=True),
        PageBreak(),
        P("3. 开发过程", "CNH1"),
        table([
            ["阶段", "主要工作"],
            ["研究与问题定义", "围绕 DApp 攻击复盘梳理跨交易故障定位、动态 Trace 和补丁验证需求。"],
            ["后端与智能体", "实现 FastAPI 服务、任务调度、MCP 工具、链上数据获取、Agent 状态和结构化报告。"],
            ["前端工作台", "实现交易输入、案例选择、任务控制台、攻击路径、Agent 日志、报告和教学视图。"],
            ["数据与部署", "接入 PostgreSQL、Redis、Docker Compose，准备 SushiSwap、ApeCoin 等缓存案例。"],
            ["验证与整理", "通过报告、日志、补丁重放和前端构建检查流程，并整理部署说明与演示材料。"],
        ], [38 * mm, 122 * mm], small=True),
        P("3.2 所使用的技术和系统架构", "CNH2"),
        P("视图层使用 React 18、Vite 5 和 TypeScript 5；业务逻辑层使用 FastAPI、Pydantic、Uvicorn、WebSocket 和 MCP 工具集；多智能体负责宏观交易分析、Trace 调试、根因定位、补丁生成、重放验证和结果审查；数据层使用 PostgreSQL 15 保存任务、报告和结构化数据，Redis 7 缓存高频日志和上下文；链上数据通过 JSON-RPC、Etherscan 和 Tenderly 获取或模拟。Docker Compose 负责后端、PostgreSQL 和 Redis 的本地编排。", "CNBody"),
        P("4. 区块链技术应用", "CNH1"),
        P("4.1 使用的区块链平台和版本", "CNH2"),
        P("作品面向 Ethereum/EVM 兼容链上的真实交易数据，使用 JSON-RPC 获取交易和状态，使用 Etherscan 类接口获取合约源码与 ABI，使用 Tenderly 进行交易模拟和补丁重放。作品不是另行发行代币或搭建新链，而是把区块链公开账本、智能合约执行轨迹和模拟环境用于安全分析应用。", "CNBody"),
        P("4.2 区块链主要存储的信息类型", "CNH2"),
        P("平台主要读取并组织交易 Hash、区块和交易收据、事件日志、调用关系、资金变化、合约地址、函数签名、源码位置、状态读写、攻击阶段和补丁重放结果。任务状态、报告和日志索引保存在本地 PostgreSQL/Redis 中；链上原始证据仍以公开链上数据和对应接口返回结果为依据。", "CNBody"),
        PageBreak(),
        P("5. 作品特色与创新", "CNH1"),
        P("5.1 作品创新点", "CNH2"),
        P("第一，支持跨交易攻击复盘，把准备、触发、套利和清理阶段组织成连续攻击路径。第二，采用动态 Trace 探索，在减少噪声和上下文压力的同时保留关键调用、状态和资金证据。第三，把补丁生成与攻击重放验证纳入同一闭环，使结论不仅来自模型推理，还能接受执行层面的检查。第四，把完成的复盘报告沉淀为可检索知识，服务后续分析、开发者修复和安全教学。", "CNBody"),
        P("5.2 与同类作品的区别与优势", "CNH2"),
        P("传统工具通常提供交易浏览、Trace 展示或静态候选位置，用户仍需人工串联证据。AttackPilot 以任务为中心统一交易输入、智能体过程、证据审查、补丁验证和报告导出，强调可追溯和可复核。项目报告中的离线评测显示，149 个真实 DApp 安全事件上，核心算法 Recall@Top-1 为 71.14%，Precision 为 77.78%；这些数字只适用于报告所述实验设置，正式提交时应以团队可核验的实验材料为准。", "CNBody"),
        P("5.3 作品难点及解决方案", "CNH2"),
        P("难点包括跨交易关系复杂、Trace 体量大、模型输出可能不可执行、长任务需要恢复以及证据链容易断裂。对应方案是使用角色与资金流构建交易上下文，以外部调用为骨架按需展开 Trace，用编译和模拟反馈修正补丁，使用 PostgreSQL/Redis 持久化任务和日志，并把结论绑定到交易 Hash、Trace 节点、源码位置和验证状态。", "CNBody"),
        P("6. 创作总结", "CNH1"),
        P("6.1 收获与经验", "CNH2"),
        P("开发过程中形成了从研究方法到可运行平台的工程化经验：复杂分析任务需要稳定的数据模型、明确的状态边界和可恢复的任务机制；界面不只展示最终答案，还应帮助用户理解证据、过程和不确定性。", "CNBody"),
        P("6.2 区块链使用的体会与反思", "CNH2"),
        P("区块链数据公开、可追溯，但公开不等于易理解。交易、事件、内部调用和状态变化必须经过结构化组织才能服务于安全判断。模型可以提高阅读和归纳效率，但不应替代链上事实和可执行验证。", "CNBody"),
        P("6.3 不足与改进方向", "CNH2"),
        P("当前版本仍依赖外部 RPC、扫描器、模拟服务和模型接口；完整新案例分析需要相应密钥和网络条件，演示主要依赖已导入缓存案例。后续将完善多链适配、权限和密钥管理、编译器按需获取、更多可量化评测、在线部署和报告模板化导出。", "CNBody"),
        P("7. 参考资料与第三方资源", "CNH1"),
        P("参考资料包括 TracePilot: Self-verifiable Framework for Decentralized Applications Fault Localization across Transactions（ISSTA 2026）、FAULTSEEKER（ASE 2025）、DAppFL（ISSTA 2024）、DeFiHackLabs、Phalcon、Tenderly、Ethernaut、Solidity 官方文档以及 DeepSeek 官方模型文档。使用的前端、后端、数据库、Docker、MCP 和区块链工具均应在正式提交前按照各自许可证完成核对。", "CNBody"),
        P("附录：成品展示与部署材料", "CNH1"),
    ]
    add_image(story, MEDIA / "image2.png", 150 * mm, "图 1  AttackPilot 技术架构")
    add_image(story, MEDIA / "image1.png", 150 * mm, "图 2  AttackPilot 产品首页"),
    add_image(story, MEDIA / "image3.png", 145 * mm, "图 3  用户功能模块"),
    add_image(story, MEDIA / "image4.png", 150 * mm, "图 4  多智能体系统流程"),
    add_image(story, MEDIA / "image6.png", 150 * mm, "图 5  任务输入工作区"),
    add_image(story, MEDIA / "image7.png", 150 * mm, "图 6  漏洞教学区"),
    add_image(story, MEDIA / "image8.png", 150 * mm, "图 7  分析复盘区"),
    doc = SimpleDocTemplate(str(path), pagesize=A4, rightMargin=18 * mm, leftMargin=18 * mm, topMargin=17 * mm, bottomMargin=18 * mm)
    doc.build(story, onFirstPage=header_footer, onLaterPages=header_footer)


def build_info_pdf(path):
    story = [Spacer(1, 13 * mm), P("AttackPilot 作品信息概要表", "CNTitle"), P("2026 年第三届全国大学生数智链应用大赛", "CNSubtitle")]
    story += [table([
        ["作品名称", "AttackPilot"],
        ["所选分类", "技术应用—应用实践与应用设计"],
        ["学校名称", "中山大学"],
        ["队长姓名", "待填写"],
        ["团队成员", "待填写"],
        ["指导教师", "待填写"],
        ["提交日期", "2026 年待填写"],
    ], [38 * mm, 122 * mm], header=False)]
    story += [P("作品简介（200 字以内）", "CNH1"), P("AttackPilot 是面向链上安全事件的攻击复盘平台。用户输入单笔或多笔交易 Hash，或选择 SushiSwap、ApeCoin 等缓存案例，系统通过链上取证、跨交易分析、动态 Trace 探索、多智能体协作和补丁重放验证，形成带有交易 Hash、Trace 节点、函数位置和验证结果的结构化复盘报告。平台服务于安全分析、合约修复和区块链安全教学。", "CNBody")]
    story += [P("创新点（300 字以内）", "CNH1"), P("作品将安全专家的“提出假设—查找证据—验证结论—修正判断”过程转化为可观察的多智能体闭环。相比只展示交易或输出可疑候选位置的工具，AttackPilot 结合跨交易上下文、动态 Trace 探索和补丁重放验证，持续缩小根因范围并检查修复是否真正阻断攻击；同时把完成的复盘结果沉淀为可检索的漏洞案例知识，支持后续分析、开发者修复和教学复用。", "CNBody")]
    story += [P("区块链应用", "CNH1"), table([
        ["平台/工具", "版本或接口", "用途"],
        ["Ethereum/EVM 兼容链", "按案例实际网络", "读取真实交易、事件、调用和状态证据"],
        ["JSON-RPC", "OpenRPC 接口", "获取链上交易和状态数据"],
        ["Etherscan 类接口", "API v2", "获取合约源码、ABI 和元数据"],
        ["Tenderly", "Simulation API", "重放原始攻击并验证补丁"],
    ], [42 * mm, 38 * mm, 80 * mm], small=True)]
    story += [P("作品原创性声明", "CNH1"), P("本团队保证以上信息真实、准确，参赛作品为本团队原创。队长签字：____________________    日期：______年____月____日", "CNBody")]
    doc = SimpleDocTemplate(str(path), pagesize=A4, rightMargin=18 * mm, leftMargin=18 * mm, topMargin=17 * mm, bottomMargin=18 * mm)
    doc.build(story, onFirstPage=header_footer, onLaterPages=header_footer)


def build_commitment_pdf(path):
    story = [Spacer(1, 22 * mm), P("全国大学生数智链应用大赛参赛作品", "CNTitle"), P("著作权授权声明", "CNSubtitle")]
    text = (
        "作品名称《AttackPilot》及其附件，是我团队在“2026 年（第 3 届）全国大学生数智链应用大赛”的参赛作品，"
        "本团队对其拥有完全的和独立的知识产权。本团队同意全国大学生数智链应用大赛组织委员会将上述作品及本团队撰写的相关说明文字，"
        "收录到组织委员会编写的参赛指南和其他相关作品中，以纸介质出版物、电子出版物或网络出版物的形式予以出版发行，且组织委员会无需向本人支付任何费用。"
    )
    story += [P(text, "CNBody"), Spacer(1, 9 * mm), P("授权人（参赛团队全体成员）签字：", "CNBody"), Spacer(1, 15 * mm)]
    story += [table([
        ["学校", "中山大学"],
        ["学院", "待填写"],
        ["专业", "待填写"],
        ["队长及成员签字", "1. ____________________\n2. ____________________\n3. ____________________\n4. ____________________\n5. ____________________"],
        ["日期", "2026 年 ____ 月 ____ 日"],
    ], [40 * mm, 120 * mm], header=False)]
    story += [Spacer(1, 9 * mm), P("此文件为待打印、手写签字并扫描的草稿，不能直接作为最终承诺书上传。", "CNSmall")]
    doc = SimpleDocTemplate(str(path), pagesize=A4, rightMargin=22 * mm, leftMargin=22 * mm, topMargin=18 * mm, bottomMargin=18 * mm)
    doc.build(story, onFirstPage=header_footer, onLaterPages=header_footer)


def sanitize_settings(path):
    text = path.read_text(encoding="utf-8", errors="replace")
    text = re.sub(r"(apikey=)[^'\"]+", r"\1YOUR_ETHERSCAN_API_KEY", text)
    text = re.sub(r"https://mainnet\.chainnodes\.org/[^'\"]+", "https://mainnet.chainnodes.org/YOUR_RPC_TOKEN", text)
    text = re.sub(r"https://bsc-mainnet\.chainnodes\.org/[^'\"]+", "https://bsc-mainnet.chainnodes.org/YOUR_RPC_TOKEN", text)
    path.write_text(text, encoding="utf-8")


def sanitize_text_files(root):
    text_suffixes = {".py", ".js", ".ts", ".tsx", ".md", ".json", ".yml", ".yaml", ".toml", ".txt"}
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in text_suffixes:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        text = re.sub(r"https://mainnet\.chainnodes\.org/[0-9a-f-]+", "https://mainnet.chainnodes.org/YOUR_RPC_TOKEN", text)
        text = re.sub(r"https://bsc-mainnet\.chainnodes\.org/[0-9a-f-]+", "https://bsc-mainnet.chainnodes.org/YOUR_RPC_TOKEN", text)
        text = re.sub(r"(apikey=)(?!YOUR_ETHERSCAN_API_KEY)[A-Za-z0-9]+", r"\1YOUR_ETHERSCAN_API_KEY", text, flags=re.I)
        path.write_text(text, encoding="utf-8")


def build_source_zip(zip_path):
    if STAGING.exists():
        shutil.rmtree(STAGING)
    STAGING.mkdir(parents=True)
    ignore_dirs = {"compiler", "node_modules", "__pycache__", ".git", "tmp"}
    for src in SOURCE.rglob("*"):
        rel = src.relative_to(SOURCE)
        if any(part in ignore_dirs for part in rel.parts):
            continue
        if rel.name in {"SignItem.csv", "model.pth", ".env"}:
            continue
        dst = STAGING / rel
        if src.is_dir():
            dst.mkdir(parents=True, exist_ok=True)
        else:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
    compiler_readme = STAGING / "backend" / "compiler" / "readme.md"
    compiler_readme.parent.mkdir(parents=True, exist_ok=True)
    compiler_readme.write_text(
        "提交版按赛事 100MB 限制未附带全量 solc 二进制。完整开发目录中包含多版本编译器；评审如需新案例的补丁编译，可按 backend/daos/contract.py 的版本解析规则补充对应版本。\n",
        encoding="utf-8",
    )
    settings = STAGING / "backend" / "settings.py"
    if settings.exists():
        sanitize_settings(settings)
    sanitize_text_files(STAGING)
    (STAGING / "SUBMISSION_SOURCE_NOTES.md").write_text(
        "本压缩包为 AttackPilot 竞赛提交版源代码。为满足 100MB 限制，未包含 backend/compiler 下的全量 solc 二进制、backend/misc/SignItem.csv 和 backend/misc/model.pth；提交前请确认评审是否需要这些可再生成或可按需获取的材料。settings.py 中的接口凭据已用占位符脱敏。\n",
        encoding="utf-8",
    )
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for f in STAGING.rglob("*"):
            if f.is_file():
                z.write(f, Path("AttackPilot_源代码") / f.relative_to(STAGING))
    shutil.rmtree(STAGING)


def main():
    for d in [PACKAGE / "01_作品文件", PACKAGE / "02_作品展示", PACKAGE / "03_设计文档", PACKAGE / "04_作品信息", PACKAGE / "05_承诺书", PACKAGE / "06_源文件" / "01_技术路线图", PACKAGE / "06_源文件" / "02_效果图", PACKAGE / "06_源文件" / "03_源代码"]:
        d.mkdir(parents=True, exist_ok=True)
    design = PACKAGE / "03_设计文档" / "AttackPilot_设计说明书.pdf"
    info = PACKAGE / "04_作品信息" / "AttackPilot_作品信息概要表.pdf"
    commitment = PACKAGE / "05_承诺书" / "AttackPilot_承诺书_待签字.pdf"
    build_design_pdf(design)
    build_info_pdf(info)
    build_commitment_pdf(commitment)
    (PACKAGE / "01_作品文件" / "AttackPilot_主作品说明.md").write_text(
        "AttackPilot 是基于区块链真实交易数据的攻击复盘 Web 应用。评审运行入口：按 03_设计文档中的安装说明，在源代码包中启动本地服务。\n\n本目录暂不放入重复的源代码压缩包，核心源代码位于 06_源文件。\n",
        encoding="utf-8",
    )
    (PACKAGE / "02_作品展示" / "AttackPilot_在线演示与登录信息_待填写.md").write_text(
        "作品演示视频：待将原视频压缩至 50MB 以内后放入本目录。\n在线演示地址：待填写；账号：待填写；密码：待填写。\n当前可用本地地址：http://localhost:5173；后端文档：http://localhost:8000/docs。\n",
        encoding="utf-8",
    )
    for i in range(1, 9):
        src = MEDIA / f"image{i}.png"
        if src.exists():
            shutil.copy2(src, PACKAGE / "06_源文件" / "02_效果图" / f"AttackPilot_效果图_{i:02d}.png")
    for name in ["image2.png", "image3.png", "image4.png"]:
        src = MEDIA / name
        if src.exists():
            shutil.copy2(src, PACKAGE / "06_源文件" / "01_技术路线图" / f"AttackPilot_{name}")
    build_source_zip(PACKAGE / "06_源文件" / "AttackPilot_源代码_提交版.zip")
    (PACKAGE / "06_源文件" / "03_源代码" / "README_目录说明.md").write_text(
        "源代码已打包为上级目录的 AttackPilot_源代码_提交版.zip。该压缩包已脱敏并按 100MB 限制排除超大编译器和模型文件，详见压缩包内 SUBMISSION_SOURCE_NOTES.md。\n",
        encoding="utf-8",
    )
    print(design)
    print(info)
    print(commitment)
    print(PACKAGE / "06_源文件" / "AttackPilot_源代码_提交版.zip")


if __name__ == "__main__":
    main()
