"""Phân loại trang PDF: có text layer (text) hay là ảnh scan (scan)."""
from __future__ import annotations

from dataclasses import dataclass

from ..config import MAX_PAGES
from .render import open_pdf

TEXT_MIN_CHARS = 25


@dataclass
class PageInfo:
    page: int
    kind: str            # "text" | "scan"
    chars: int
    image_ratio: float

    def to_dict(self) -> dict:
        return {
            "page": self.page,
            "kind": self.kind,
            "chars": self.chars,
            "image_ratio": round(self.image_ratio, 3),
        }


def _image_ratio(page, width_pt: float, height_pt: float) -> float:
    """Tỉ lệ diện tích trang bị ảnh (bitmap) phủ."""
    try:
        from pypdfium2 import raw as pdfium_c
        total = 0.0
        area = max(1.0, width_pt * height_pt)
        for obj in page.get_objects():
            if getattr(obj, "type", None) != pdfium_c.FPDF_PAGEOBJ_IMAGE:
                continue
            left, bottom, right, top = obj.get_pos()
            total += max(0.0, right - left) * max(0.0, top - bottom)
        return min(1.0, total / area)
    except Exception:
        return 0.0


def classify_page(page, page_no: int) -> PageInfo:
    width_pt, height_pt = page.get_size()
    try:
        tp = page.get_textpage()
        text = tp.get_text_range() or ""
    except Exception:
        text = ""
    chars = len(text.strip())
    ratio = _image_ratio(page, width_pt, height_pt)
    kind = "text" if chars >= TEXT_MIN_CHARS else "scan"
    return PageInfo(page=page_no, kind=kind, chars=chars, image_ratio=ratio)


def analyze_pdf(path, max_pages: int | None = None) -> dict:
    pdf = open_pdf(path)
    try:
        n = len(pdf)
        limit = min(n, max_pages or MAX_PAGES)
        pages = [classify_page(pdf[i], i + 1) for i in range(limit)]
        kinds = {p.kind for p in pages}
        doc_kind = pages[0].kind if len(kinds) == 1 and pages else "mixed"
        return {
            "num_pages": n,
            "analyzed_pages": limit,
            "doc_kind": doc_kind,
            "pages": [p.to_dict() for p in pages],
        }
    finally:
        pdf.close()
