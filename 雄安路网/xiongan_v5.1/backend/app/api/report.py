"""评估报告 API：按模拟时间区间生成并下载 md / docx / pdf 报告。"""

from urllib.parse import quote

from fastapi import APIRouter, Body, Request
from fastapi.responses import StreamingResponse

from app.report.generator import ReportGenerator

router = APIRouter(prefix="/report", tags=["report"])

ALL_SECTIONS = ["overview", "score", "history", "intersections", "charts", "events"]


@router.post("/generate")
def generate_report(request: Request, body: dict = Body(...)):
    """生成评估报告。

    body: {format: 'md'|'docx'|'pdf', start: 秒, end: 秒,
           sections: ['overview','score','history','intersections','charts','events']}
    """
    runtime = request.app.state.runtime
    fmt = str(body.get("format", "md")).lower()
    if fmt not in ("md", "docx", "pdf"):
        return {"code": 2001, "message": f"不支持的格式: {fmt}", "data": None}
    start = max(0, int(body.get("start", 0) or 0))
    end = int(body.get("end", 0) or 0)
    if end <= 0:
        end = runtime.status().get("step", 0) or 0
    if end <= start:
        return {"code": 2002, "message": "评估结束时间必须大于开始时间", "data": None}
    sections = [s for s in (body.get("sections") or ALL_SECTIONS) if s in ALL_SECTIONS]
    if not sections:
        sections = ALL_SECTIONS

    try:
        filename, content = ReportGenerator(runtime).generate(fmt, start, end, sections)
    except RuntimeError as exc:
        # 缺 matplotlib / python-docx / reportlab 等依赖时给出安装提示
        return {"code": 2003, "message": str(exc), "data": None}
    media = {
        "md": "text/markdown; charset=utf-8",
        "docx": "application/vnd.openxmlformats-officedocument."
                "wordprocessingml.document",
        "pdf": "application/pdf",
    }[fmt]
    quoted = quote(filename)
    return StreamingResponse(
        iter([content]),
        media_type=media,
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quoted}"},
    )
