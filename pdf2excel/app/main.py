"""FastAPI app: API + giao diện web tĩnh."""
from __future__ import annotations

import io
import json
import logging
import zipfile
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from . import __version__
from .config import LOG_DIR, MAX_FILES, MAX_UPLOAD_MB, WEB_DIR
from .core.render import open_pdf, render_page, save_png
from .jobs import manager

# Ghi log ra file để có thể chẩn đoán khi người dùng báo "treo".
# Chỉ dùng FileHandler: console Windows (cp1252) có thể làm hỏng log tiếng Trung.
LOG_DIR.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    handlers=[logging.FileHandler(LOG_DIR / "app.log", encoding="utf-8")],
)
logging.getLogger("app").setLevel(logging.INFO)

app = FastAPI(title="PDF → Excel", version=__version__)


if WEB_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")


# ----------------------------------------------------------------- khung dữ liệu
class ConvertBody(BaseModel):
    mode: str = "auto"           # auto | text | ocr
    engine: str = "fast"         # fast | deep
    page_modes: dict[str, str] = {}
    skip_first_page: bool = False  # bỏ trang 1 (bìa / bản vẽ AutoCAD)


class ExportBody(BaseModel):
    include: list[str] | None = None
    edits: dict[str, dict[str, str]] | None = None


class ZipBody(BaseModel):
    ids: list[str]


# --------------------------------------------------------------------- tiện ích
def _job_or_404(job_id: str):
    job = manager.get(job_id)
    if job is None:
        raise HTTPException(404, "Không tìm thấy job (có thể đã bị xoá).")
    return job


def _page_image(job, page_no: int) -> Path:
    png = job.workdir / "pages" / f"p{page_no}.png"
    if png.exists():
        return png
    try:
        pdf = open_pdf(job.path)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, f"Không mở được PDF: {exc}") from exc
    try:
        if not (1 <= page_no <= len(pdf)):
            raise HTTPException(404, "Trang không tồn tại.")
        img = render_page(pdf[page_no - 1], 110)
        png.parent.mkdir(parents=True, exist_ok=True)
        save_png(img, png, max_width=1000)
    finally:
        pdf.close()
    return png


# --------------------------------------------------------------------- endpoint
@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(WEB_DIR / "index.html")


@app.get("/api/health")
def health() -> dict:
    return {"ok": True, "version": __version__}


@app.get("/favicon.ico", include_in_schema=False)
def favicon() -> Response:
    return Response(status_code=204)


@app.post("/api/jobs")
async def upload(files: list[UploadFile] = File(...)) -> dict:
    if not files:
        raise HTTPException(400, "Chưa chọn file nào.")
    if len(files) > MAX_FILES:
        raise HTTPException(400, f"Tối đa {MAX_FILES} file mỗi lần.")

    limit = MAX_UPLOAD_MB * 1024 * 1024
    out = []
    for f in files:
        data = await f.read()
        if len(data) > limit:
            raise HTTPException(
                413, f'"{f.filename}" vượt quá {MAX_UPLOAD_MB} MB.'
            )
        if not data.startswith(b"%PDF"):
            raise HTTPException(400, f'"{f.filename}" không phải file PDF.')
        job = manager.create(f.filename or "document.pdf", data)
        # phân tích trong thread riêng: PDF scan lớn không được chặn event loop
        info = await run_in_threadpool(manager.analyze, job)
        out.append({**job.snapshot(), **info})
    return {"jobs": out}


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str) -> dict:
    job = _job_or_404(job_id)
    data = job.snapshot()
    if job.result is not None:
        data["result"] = job.result.to_dict()
    return data


@app.post("/api/jobs/{job_id}/convert")
def start_convert(job_id: str, body: ConvertBody) -> dict:
    job = _job_or_404(job_id)
    page_modes = {}
    for k, v in (body.page_modes or {}).items():
        try:
            page_modes[int(k)] = v
        except (TypeError, ValueError):
            continue
    return manager.start(
        job, mode=body.mode, engine=body.engine, page_modes=page_modes,
        skip_first_page=body.skip_first_page,
    )


@app.post("/api/jobs/{job_id}/cancel")
def cancel_job(job_id: str) -> dict:
    job = _job_or_404(job_id)
    return manager.cancel(job)


@app.get("/api/jobs/{job_id}/events")
def events(job_id: str, request: Request) -> StreamingResponse:
    job = _job_or_404(job_id)

    def gen():
        sent = 0
        while True:
            if request and getattr(request, "is_disconnected", None) is not None:
                pass
            batch = manager.wait_events(job, sent, timeout=15.0)
            for ev in batch:
                sent += 1
                yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
            if not batch:
                yield ": ping\n\n"
            if job.status in ("done", "error") and sent >= len(job.events):
                break

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/jobs/{job_id}/page/{page_no}")
def page_png(job_id: str, page_no: int) -> FileResponse:
    job = _job_or_404(job_id)
    png = _page_image(job, page_no)
    return FileResponse(png, media_type="image/png")


@app.post("/api/jobs/{job_id}/export")
def export(job_id: str, body: ExportBody) -> dict:
    job = _job_or_404(job_id)
    if job.result is None:
        raise HTTPException(409, "Chưa chuyển đổi xong.")
    try:
        path = manager.export(job, include=body.include, edits=body.edits)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(500, f"Lỗi khi tạo Excel: {exc}") from exc
    return {"ok": True, "filename": path.name, "url": f"/api/jobs/{job_id}/download"}


@app.get("/api/jobs/{job_id}/download")
def download(job_id: str) -> FileResponse:
    job = _job_or_404(job_id)
    if job.result is None:
        raise HTTPException(409, "Chưa chuyển đổi xong.")
    # Chưa xuất (hoặc file đã bị xoá) thì tự xuất — tránh lỗi 404 khó hiểu
    if job.xlsx_path is None or not job.xlsx_path.exists():
        try:
            manager.export(job)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(500, f"Lỗi khi tạo Excel: {exc}") from exc
    return FileResponse(
        job.xlsx_path,
        filename=job.xlsx_path.name,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@app.post("/api/export-zip")
def export_zip(body: ZipBody) -> Response:
    jobs = []
    for jid in body.ids:
        job = manager.get(jid)
        if job is None or job.result is None:
            continue
        if job.xlsx_path is None or not job.xlsx_path.exists():
            try:
                manager.export(job)
            except Exception:  # noqa: BLE001
                continue
        jobs.append(job)
    if not jobs:
        raise HTTPException(400, "Không có file nào sẵn sàng để tải.")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        used: set[str] = set()
        for job in jobs:
            name = job.xlsx_path.name
            n = 2
            while name in used:
                name = f"{job.xlsx_path.stem}_{n}.xlsx"
                n += 1
            used.add(name)
            zf.write(job.xlsx_path, arcname=name)
    buf.seek(0)
    return Response(
        buf.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="pdf2excel.zip"'},
    )


@app.delete("/api/jobs/{job_id}")
def delete_job(job_id: str) -> dict:
    job = _job_or_404(job_id)
    manager.delete(job)
    return {"ok": True}
