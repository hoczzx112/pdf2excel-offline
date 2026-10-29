"""Điều phối: PDF -> danh sách trang -> bảng -> (xlsx ở tầng export)."""
from __future__ import annotations

import logging
import time
from collections.abc import Callable
from pathlib import Path

import numpy as np

from ..config import DPI, MAX_PAGES
from .cellcomponents import page_cell_rects
from .detect import classify_page
from .gridlines import to_binary
from .models import DocResult, PageResult, Table, TextItem
from .ocr import items_to_text, ocr_items
from .render import deskew, estimate_skew, open_pdf, render_page, save_png
from .tables import build_page_tables, whitespace_tables
from .textlayer import page_text_items

log = logging.getLogger(__name__)

Event = Callable[..., None]


class Cancelled(Exception):
    """Người dùng bấm Dừng giữa chừng."""


def _noop(**_kwargs) -> None:
    return None


def _emit(cb: Event, **data) -> None:
    try:
        cb(**data)
    except Exception:
        pass


def _page_tables(
    img: np.ndarray, items: list[TextItem], page_no: int, engine: str
) -> tuple[list[Table], list[str]]:
    warnings: list[str] = []
    binary = to_binary(img)
    rects = page_cell_rects(binary, items, remove_text=(engine == "text"))
    tables = build_page_tables(rects, items, page_no, engine, img.shape[0]) if rects else []
    if not tables:
        tables = whitespace_tables(items, page_no, engine)
        if tables:
            warnings.append(
                "Không thấy đường kẻ bảng — đã chia cột theo khoảng trắng (kết quả có thể lệch)."
            )
        else:
            warnings.append("Không phát hiện bảng nào trên trang.")
    return tables, warnings


def convert(
    path,
    *,
    mode: str = "auto",
    page_modes: dict[int, str] | None = None,
    engine: str = "fast",
    dpi: int = DPI,
    on_event: Event | None = None,
    workdir: Path | None = None,
    max_pages: int | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> DocResult:
    """Chuyển một file PDF thành DocResult.

    mode: auto | text (buộc đọc text layer) | ocr (buộc OCR)
    engine: fast (mặc định) | deep (docling, chậm nhưng xử lý layout lạ tốt hơn)
    should_stop: hàm trả về True để dừng giữa các trang (nút "Dừng").
    """
    if engine == "deep":
        from .docling_engine import convert_deep

        return convert_deep(
            path, on_event=on_event, workdir=workdir, max_pages=max_pages
        )

    cb = on_event or _noop
    stop = should_stop or (lambda: False)
    path = Path(path)
    pdf = open_pdf(path)
    result = DocResult(filename=path.name)
    scale = dpi / 72.0
    t_all = time.time()

    try:
        total = min(len(pdf), max_pages or MAX_PAGES)
        if len(pdf) > total:
            result.warnings.append(
                f"File có {len(pdf)} trang, chỉ xử lý {total} trang đầu (giới hạn cấu hình)."
            )

        for i in range(total):
            if stop():
                raise Cancelled()

            page = pdf[i]
            page_no = i + 1
            t_page = time.time()
            info = classify_page(page, page_no)

            kind = info.kind
            override = (page_modes or {}).get(page_no)
            if override in ("text", "scan"):
                kind = override
            elif mode in ("text", "ocr"):
                kind = "text" if mode == "text" else "scan"

            warnings: list[str] = []
            if kind == "text" and info.chars < 5:
                kind = "scan"
                warnings.append("Trang không có text layer — tự chuyển sang OCR.")

            _emit(
                cb,
                stage="page",
                page=page_no,
                pages=total,
                pct=5 + 90 * (i / max(1, total)),
                message=f"Trang {page_no}/{total}: {'đọc text layer' if kind == 'text' else 'OCR ảnh'}…",
            )

            t = time.time()
            img = render_page(page, dpi)
            t_render = time.time() - t
            t_ocr = t_skew = 0.0

            if kind == "scan":
                t = time.time()
                angle = estimate_skew(to_binary(img))
                if abs(angle) >= 0.25:
                    img = deskew(img, angle)
                    warnings.append(f"Ảnh nghiêng {angle:.1f}° — đã tự xoay thẳng.")
                t_skew = time.time() - t

                t = time.time()
                items = ocr_items(img)
                t_ocr = time.time() - t
                text = items_to_text(items)
                if not items:
                    warnings.append("OCR không đọc được chữ nào trên trang.")
                used = "ocr"
            else:
                items = page_text_items(page, scale)
                text = page.get_textpage().get_text_range() or ""
                used = "text"

            t = time.time()
            tables, w2 = _page_tables(img, items, page_no, used)
            t_grid = time.time() - t
            warnings.extend(w2)

            if workdir:
                try:
                    (Path(workdir) / "pages").mkdir(parents=True, exist_ok=True)
                    save_png(img, Path(workdir) / "pages" / f"p{page_no}.png")
                except Exception:
                    pass

            result.pages.append(
                PageResult(
                    page=page_no,
                    kind=info.kind,
                    used=used,
                    width_pt=page.get_size()[0],
                    height_pt=page.get_size()[1],
                    tables=tables,
                    text=text.strip(),
                    elapsed=time.time() - t_page,
                    warnings=warnings,
                )
            )

            log.info(
                "page %d/%d used=%s render=%.2fs skew=%.2fs ocr=%.2fs grid=%.2fs tables=%d total=%.2fs",
                page_no, total, used, t_render, t_skew, t_ocr, t_grid,
                len(tables), time.time() - t_page,
            )

            _emit(
                cb,
                stage="page_done",
                page=page_no,
                pages=total,
                pct=5 + 90 * ((i + 1) / max(1, total)),
                message=f"Trang {page_no}: {len(tables)} bảng",
            )
    finally:
        pdf.close()

    result.duration = time.time() - t_all
    log.info("done %s: %d trang, %d bảng, %.1fs",
             path.name, len(result.pages), len(result.tables), result.duration)
    _emit(cb, stage="done", pct=100, message="Hoàn tất")
    return result
