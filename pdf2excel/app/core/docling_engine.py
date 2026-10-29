"""Engine "deep": dùng docling (layout model + TableFormer).

Chậm hơn nhiều so với engine mặc định (vài chục giây/trang trên CPU) nhưng
xử lý tốt các tài liệu có bố cục lạ, bảng lồng nhau, nhiều cột.
Chỉ nạp khi người dùng chọn, để app khởi động nhanh.
"""
from __future__ import annotations

import time
from pathlib import Path

from .models import DocResult, PageResult, Table, TableCell

_converter = None


def _get_converter():
    global _converter
    if _converter is not None:
        return _converter

    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions
    from docling.document_converter import DocumentConverter, PdfFormatOption

    opts = PdfPipelineOptions()
    opts.do_ocr = False
    opts.do_table_structure = True
    try:
        opts.generate_page_images = False
        opts.generate_picture_images = False
    except Exception:
        pass

    _converter = DocumentConverter(
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts)}
    )
    return _converter


def _cell_grid(td) -> tuple[int, int, list[TableCell]]:
    rows = getattr(td, "num_rows", 0)
    cols = getattr(td, "num_cols", 0)
    cells: list[TableCell] = []
    seen: set[tuple[int, int]] = set()
    grid = getattr(td, "grid", None) or []
    for r_idx, row in enumerate(grid):
        for cell in row:
            cr = getattr(cell, "start_row_offset_idx", r_idx)
            cc = getattr(cell, "start_col_offset_idx", 0)
            key = (cr, cc)
            if key in seen:
                continue
            seen.add(key)
            text = (getattr(cell, "text", "") or "").strip()
            if not text:
                continue
            cells.append(
                TableCell(
                    row=cr,
                    col=cc,
                    rowspan=max(1, int(getattr(cell, "row_span", 1) or 1)),
                    colspan=max(1, int(getattr(cell, "col_span", 1) or 1)),
                    text=text,
                )
            )
    if not rows:
        rows = max((c.row + c.rowspan for c in cells), default=0)
    if not cols:
        cols = max((c.col + c.colspan for c in cells), default=0)
    return rows, cols, cells


def convert_deep(
    path,
    *,
    on_event=None,
    workdir: Path | None = None,
    max_pages: int | None = None,
) -> DocResult:
    from docling_core.types.doc.document import TableItem as DocTableItem
    from docling_core.types.doc.document import TextItem as DocTextItem

    cb = on_event or (lambda **_k: None)
    path = Path(path)
    result = DocResult(filename=path.name)
    t0 = time.time()

    cb(stage="docling", pct=8, message="Nạp model docling (lần đầu có thể mất vài phút)…")
    converter = _get_converter()
    cb(stage="docling", pct=20, message="Đang phân tích bố cục tài liệu…")
    conv_res = converter.convert(str(path))
    doc = conv_res.document

    texts: dict[int, list[str]] = {}
    tables_by_page: dict[int, list[Table]] = {}

    for item, _level in doc.iterate_items():
        prov = getattr(item, "prov", None) or []
        page_no = getattr(prov[0], "page_no", 1) if prov else 1
        if isinstance(item, DocTableItem):
            rows, cols, cells = _cell_grid(item.data)
            if not cells or rows < 1 or cols < 1:
                continue
            lst = tables_by_page.setdefault(page_no, [])
            lst.append(
                Table(
                    page=page_no,
                    index=len(lst) + 1,
                    num_rows=rows,
                    num_cols=cols,
                    cells=cells,
                    engine="docling",
                )
            )
        elif isinstance(item, DocTextItem):
            txt = (getattr(item, "text", "") or "").strip()
            if txt:
                texts.setdefault(page_no, []).append(txt)

    total = max(list(tables_by_page) + list(texts) + [1])
    for page_no in range(1, total + 1):
        result.pages.append(
            PageResult(
                page=page_no,
                kind="text",
                used="docling",
                width_pt=0.0,
                height_pt=0.0,
                tables=tables_by_page.get(page_no, []),
                text="\n".join(texts.get(page_no, [])),
            )
        )

    if not any(p.tables for p in result.pages):
        result.warnings.append(
            "docling không tìm thấy bảng nào. Thử lại ở chế độ Nhanh."
        )

    result.duration = time.time() - t0
    cb(stage="done", pct=100, message="Hoàn tất")
    return result
