# -*- coding: utf-8 -*-
"""Markdown → PDF 工具（中文 + 表格 + 内嵌图片）。

实现：python-markdown → 单文件 HTML（本地图转 base64 内嵌，CSS 排版 A4 中文）
→ Microsoft Edge 无头打印 PDF（Windows 自带，免装 pandoc/TeX）。

用法：
    python scripts/md2pdf.py <报告.md> [输出.pdf]
    示例：python scripts/md2pdf.py ../docs/系统设计与算法报告.md
"""
import base64
import mimetypes
import os
import re
import subprocess
import sys
import urllib.parse
import urllib.request

import markdown

CSS = """
@page { size: A4; margin: 16mm 15mm; }
* { box-sizing: border-box; }
body { font-family: "Microsoft YaHei", "PingFang SC", "SimSun", sans-serif;
       font-size: 11pt; line-height: 1.65; color: #1a1a1a; margin: 0; }
h1 { font-size: 19pt; border-bottom: 2px solid #2f9e44; padding-bottom: 6px; margin: 22px 0 12px; }
h2 { font-size: 15pt; color: #14532d; border-left: 4px solid #2f9e44; padding-left: 8px; margin: 18px 0 8px; }
h3 { font-size: 12.5pt; margin: 14px 0 6px; }
p  { margin: 6px 0; }
table { border-collapse: collapse; width: 100%; margin: 8px 0; font-size: 9.5pt; page-break-inside: avoid; }
th, td { border: 1px solid #bbb; padding: 4px 7px; text-align: left; }
th { background: #eef7ee; font-weight: 600; }
tr:nth-child(even) td { background: #fafcfa; }
pre { background: #f6f8f6; border: 1px solid #ddd; border-radius: 5px; padding: 8px;
      font-family: Consolas, "Courier New", monospace; font-size: 9pt; white-space: pre-wrap;
      word-break: break-all; page-break-inside: avoid; }
code { font-family: Consolas, "Courier New", monospace; background: #f1f3f1; padding: 0 3px;
       border-radius: 3px; font-size: 9.5pt; }
pre code { background: none; padding: 0; }
blockquote { margin: 8px 0; padding: 4px 12px; border-left: 4px solid #2f9e44;
             background: #f6faf6; color: #333; }
img { max-width: 100%; height: auto; display: block; margin: 8px auto; }
hr { border: none; border-top: 1px solid #ccc; margin: 14px 0; }
li { margin: 2px 0; }
"""


def inline_images(html: str, md_dir: str) -> str:
    """把本地图片转为 base64 data URI 内嵌（HTML 单文件化，PDF 不依赖相对路径）。"""

    def rep(m):
        alt, path = m.group(1), m.group(2).strip()
        if path.startswith(("http://", "https://", "data:")):
            return m.group(0)
        p = path if os.path.isabs(path) else os.path.join(md_dir, path)
        if not os.path.isfile(p):
            return f'<img alt="{alt}" style="color:#c00">[{path} 缺失]'
        b64 = base64.b64encode(open(p, "rb").read()).decode("ascii")
        mime = mimetypes.guess_type(p)[0] or "image/png"
        return f'<img alt="{alt}" src="data:{mime};base64,{b64}">'

    return re.sub(r"!\[([^\]]*)\]\(([^)\s]+)(?:\s+[^)]*)?\)", rep, html)


def convert(md_path: str, out_pdf: str) -> None:
    md_abs = os.path.abspath(md_path)
    out_abs = os.path.abspath(out_pdf)
    md_dir = os.path.dirname(md_abs)
    text = open(md_abs, encoding="utf-8").read()
    body = markdown.markdown(text, extensions=["tables", "fenced_code", "sane_lists"])
    body = inline_images(body, md_dir)
    html = f"<!DOCTYPE html><html><head><meta charset='utf-8'><style>{CSS}</style></head><body>{body}</body></html>"
    html_path = md_abs + ".tmp.html"
    open(html_path, "w", encoding="utf-8").write(html)

    edge = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
    if not os.path.isfile(edge):
        edge = r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"
    file_url = urllib.parse.urljoin("file:", urllib.request.pathname2url(html_path))
    profile = os.path.join(os.environ.get("TEMP", "/tmp"), "edge_pdf_profile")
    subprocess.run([edge, "--headless", "--disable-gpu", "--no-pdf-header-footer",
                    f"--user-data-dir={profile}", f"--print-to-pdf={out_abs}",
                    file_url], check=True, capture_output=True, timeout=180)
    os.remove(html_path)
    print(f"OK -> {out_abs} ({os.path.getsize(out_abs)//1024} KB)")


if __name__ == "__main__":
    md = sys.argv[1]
    out = sys.argv[2] if len(sys.argv) > 2 else md[:-3] + ".pdf"
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    convert(md, out)
