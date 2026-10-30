"""Quản lý job: lưu file, chạy pipeline nền, phát tiến độ, xuất Excel."""
from __future__ import annotations

import logging
import shutil
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from ..config import JOBS_DIR
from ..core.detect import analyze_pdf
from ..core.extract import Cancelled, convert
from ..core.models import DocResult
from ..export.xlsx import save_workbook

log = logging.getLogger(__name__)

_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="pdf2xlsx")
_lock = threading.Lock()

TICK_SECONDS = 1.2          # nhịp phát sự kiện "đang chạy" để UI không bị đứng


@dataclass
class Job:
    id: str
    filename: str
    size: int
    path: Path
    workdir: Path
    status: str = "uploaded"   # uploaded|analyzing|ready|converting|done|cancelled|error
    analysis: dict | None = None
    result: DocResult | None = None
    error: str | None = None
    options: dict = field(default_factory=dict)
    events: list[dict] = field(default_factory=list)
    xlsx_path: Path | None = None
    created: float = field(default_factory=time.time)
    finished: float | None = None
    cond: threading.Condition = field(default_factory=threading.Condition, repr=False)

    # --- trạng thái nội bộ cho heartbeat ---
    cancel_requested: bool = False
    hb_stop: bool = False
    t_start: float = 0.0
    page_no: int = 0
    pages_total: int = 0
    first_page: int = 1
    pages_done: int = 0
    page_started: float = 0.0
    page_durations: list[float] = field(default_factory=list)
    stage_message: str = ""
    heartbeat: threading.Thread | None = field(default=None, repr=False)

    @property
    def stem(self) -> str:
        return Path(self.filename).stem or self.id

    def snapshot(self) -> dict:
        return {
            "id": self.id,
            "filename": self.filename,
            "size": self.size,
            "status": self.status,
            "error": self.error,
            "analysis": self.analysis,
            "options": self.options,
            "progress": self.events[-1] if self.events else None,
            "has_result": self.result is not None,
            "xlsx_ready": self.xlsx_path is not None and self.xlsx_path.exists(),
            "elapsed": round((self.finished or time.time()) - self.created, 1),
        }


class JobManager:
    def __init__(self) -> None:
        self.jobs: dict[str, Job] = {}

    # ---------- tạo / đọc ----------
    def create(self, filename: str, data: bytes) -> Job:
        job_id = uuid.uuid4().hex[:12]
        workdir = JOBS_DIR / job_id
        (workdir / "pages").mkdir(parents=True, exist_ok=True)
        safe = Path(filename).name or f"{job_id}.pdf"
        path = workdir / "orig.pdf"
        path.write_bytes(data)
        job = Job(
            id=job_id, filename=safe, size=len(data), path=path, workdir=workdir
        )
        with _lock:
            self.jobs[job_id] = job
        return job

    def get(self, job_id: str) -> Job | None:
        return self.jobs.get(job_id)

    # ---------- sự kiện / tiến độ ----------
    def emit(self, job: Job, **event: object) -> None:
        with job.cond:
            event["seq"] = len(job.events) + 1
            event.setdefault("ts", round(time.time() - job.created, 2))
            job.events.append(event)
            job.cond.notify_all()

    def wait_events(self, job: Job, after: int, timeout: float = 20.0) -> list[dict]:
        with job.cond:
            if len(job.events) <= after:
                job.cond.wait(timeout)
            return job.events[after:]

    # ---------- phân tích ----------
    def analyze(self, job: Job) -> dict:
        job.status = "analyzing"
        try:
            job.analysis = analyze_pdf(job.path)
            job.status = "ready"
            log.info("analyze %s: %d trang, %s",
                     job.filename, job.analysis.get("num_pages"), job.analysis.get("doc_kind"))
        except Exception as exc:  # noqa: BLE001
            job.status = "error"
            job.error = f"Không đọc được PDF: {exc}"
            log.exception("analyze failed: %s", job.filename)
        return {"status": job.status, "analysis": job.analysis, "error": job.error}

    # ---------- chuyển đổi ----------
    def start(self, job: Job, *, mode: str = "auto", engine: str = "fast",
              page_modes: dict[int, str] | None = None,
              skip_first_page: bool = False) -> dict:
        if job.status == "converting":
            return {"ok": False, "error": "File này đang được xử lý."}

        # chuyển đổi lại: bỏ kết quả cũ
        job.result = None
        if job.xlsx_path and job.xlsx_path.exists():
            try:
                job.xlsx_path.unlink()
            except OSError:
                pass
        job.xlsx_path = None

        job.status = "converting"
        job.error = None
        job.finished = None
        job.cancel_requested = False
        job.hb_stop = False
        job.t_start = time.time()
        job.page_no = 0
        job.pages_total = 0
        job.first_page = 1
        job.pages_done = 0
        job.page_started = time.time()
        job.page_durations = []
        job.stage_message = "Đang chuẩn bị…"
        job.options = {"mode": mode, "engine": engine,
                       "skip_first_page": bool(skip_first_page),
                       "page_modes": {str(k): v for k, v in (page_modes or {}).items()}}
        self.emit(job, stage="start", pct=0, message="Bắt đầu xử lý…")

        job.heartbeat = threading.Thread(
            target=self._heartbeat, args=(job,), daemon=True, name=f"hb-{job.id}"
        )
        job.heartbeat.start()
        _executor.submit(self._run, job, mode, engine, page_modes or {},
                         bool(skip_first_page))
        log.info("job %s start: %s mode=%s engine=%s skip_first=%s pages=%s",
                 job.id, job.filename, mode, engine, bool(skip_first_page),
                 (job.analysis or {}).get("num_pages"))
        return {"ok": True}

    def cancel(self, job: Job) -> dict:
        if job.status != "converting":
            return {"ok": False, "error": "File này không đang xử lý."}
        job.cancel_requested = True
        self.emit(job, stage="cancelling", pct=job.events[-1].get("pct", 0) if job.events else 0,
                  message="Đang dừng sau khi xong trang hiện tại…")
        return {"ok": True}

    def _on_event(self, job: Job, **ev: object) -> None:
        """Ghi nhận trạng thái trang hiện tại rồi chuyển tiếp sự kiện ra UI."""
        stage = ev.get("stage")
        if stage == "page":
            job.page_no = int(ev.get("page") or 0)
            job.pages_total = int(ev.get("pages") or 0)
            job.first_page = int(ev.get("first_page") or 1)
            job.page_started = time.time()
            msg = str(ev.get("message") or "")
            if ": " in msg:                       # bỏ tiền tố "Trang 1/3: "
                msg = msg.split(": ", 1)[1]
            job.stage_message = msg.rstrip("…").strip() or "đang xử lý"
        elif stage == "page_done":
            dur = time.time() - job.page_started
            if dur > 0:
                job.page_durations.append(dur)
                job.page_durations = job.page_durations[-5:]
            job.pages_done = int(ev.get("page") or job.pages_done)
            job.stage_message = "chuẩn bị trang tiếp theo"
        self.emit(job, **ev)

    def _heartbeat(self, job: Job) -> None:
        """Phát sự kiện đều đặn để thanh tiến độ luôn nhích, kèm thời gian/ETA.

        OCR một trang scan mất 5–10 giây (trang lớn có thể lâu hơn); nếu chỉ
        phát sự kiện ở ranh giới trang thì người dùng dễ tưởng app bị treo.
        """
        while True:
            slept = 0.0
            while slept < TICK_SECONDS:
                time.sleep(0.1)
                slept += 0.1
                if job.hb_stop or job.status != "converting":
                    return
            if job.hb_stop or job.status != "converting":
                return

            elapsed = time.time() - job.t_start
            pages = job.pages_total
            first = max(1, min(job.first_page, pages or 1))
            span = max(1, pages - first + 1) if pages else 1
            done = job.pages_done - first + 1 if pages else 0
            done = max(0, min(span, done))
            if pages:
                base = 5 + 90 * (done / span)
                cap = 5 + 90 * (min(span, done + 1) / span)
                frac = min(0.9, elapsed / max(15.0, 5.0 * (done + 1)))
                pct = base + (cap - base) * frac
                msg = f"Trang {min(pages, max(first, job.page_no))}/{pages}: {job.stage_message} · {elapsed:.0f}s"
                durs = job.page_durations[-4:]
                if durs and span > done:
                    avg = sum(durs) / len(durs)
                    eta = avg * (span - done) - (time.time() - job.page_started)
                    if eta > 1:
                        msg += f" (còn ~{_fmt_eta(eta)})"
            else:
                pct = min(4.0, elapsed / 30.0)
                msg = f"{job.stage_message} · {elapsed:.0f}s"

            self.emit(job, stage="tick", pct=round(pct, 1), message=msg,
                      page=job.page_no, pages=pages, elapsed=round(elapsed, 1))

    def _run(self, job: Job, mode: str, engine: str,
             page_modes: dict[int, str], skip_first_page: bool = False) -> None:
        t0 = time.time()
        try:
            result = convert(
                job.path,
                mode=mode,
                page_modes=page_modes,
                engine=engine,
                on_event=lambda **ev: self._on_event(job, **ev),
                workdir=job.workdir,
                should_stop=lambda: job.cancel_requested,
                skip_first_page=skip_first_page,
            )
            job.result = result
            job.finished = time.time()
            job.status = "done"
            self.emit(
                job, stage="completed", pct=100,
                message=f"Xong: {len(result.tables)} bảng / {len(result.pages)} trang",
            )
        except Cancelled:
            job.status = "cancelled"
            job.finished = time.time()
            self.emit(job, stage="cancelled", pct=100, message="Đã dừng theo yêu cầu.")
        except Exception as exc:  # noqa: BLE001
            job.status = "error"
            job.error = f"{type(exc).__name__}: {exc}"
            job.finished = time.time()
            self.emit(job, stage="error", pct=100, message=job.error)
            log.exception("job %s failed", job.id)
        finally:
            job.hb_stop = True
            job.options["elapsed"] = round(time.time() - t0, 1)
            log.info("job %s %s in %.1fs%s", job.id, job.status,
                     time.time() - t0, f" ({job.error})" if job.error else "")

    # ---------- xuất Excel ----------
    def export(self, job: Job, include: list[str] | None = None,
               edits: dict | None = None) -> Path:
        if job.result is None:
            raise ValueError("Chưa có kết quả để xuất.")
        out = job.workdir / f"{job.stem}.xlsx"
        save_workbook(
            job.result, out,
            include=set(include) if include else None,
            edits=edits or {},
        )
        job.xlsx_path = out
        log.info("export %s -> %s (%d bytes)", job.id, out.name, out.stat().st_size)
        return out

    def delete(self, job: Job) -> None:
        job.cancel_requested = True
        job.hb_stop = True
        with _lock:
            self.jobs.pop(job.id, None)
        shutil.rmtree(job.workdir, ignore_errors=True)


def _fmt_eta(seconds: float) -> str:
    seconds = int(round(seconds))
    if seconds < 60:
        return f"{seconds}s"
    m, s = divmod(seconds, 60)
    return f"{m} phút" if s < 15 else f"{m} phút {s}s"


manager = JobManager()
