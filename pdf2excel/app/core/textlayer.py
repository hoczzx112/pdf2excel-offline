"""Trích chữ từ text layer của PDF và ghép chữ vào từng ô bảng.

Dùng chung cho cả hai nhánh: text layer (toạ độ ký tự từ pdfium) và OCR
(toạ độ dòng chữ từ RapidOCR). Nhờ vậy phần dựng bảng chỉ có một cài đặt.
"""
from __future__ import annotations

import re
import statistics
from collections.abc import Iterable

from .models import TextItem

_CJK_RANGES = (
    (0x2E80, 0x9FFF),    # CJK radicals .. CJK unified ideographs
    (0xAC00, 0xD7AF),    # Hangul
    (0xF900, 0xFAFF),    # CJK compatibility
    (0xFF00, 0xFFEF),    # fullwidth forms
    (0x3000, 0x303F),    # CJK punctuation
)


def is_cjk(ch: str) -> bool:
    if not ch:
        return False
    cp = ord(ch[0])
    return any(a <= cp <= b for a, b in _CJK_RANGES)


def page_text_items(page, scale: float) -> list[TextItem]:
    """Lấy từng ký tự của text layer kèm toạ độ pixel (y hướng xuống)."""
    width_pt, height_pt = page.get_size()
    tp = page.get_textpage()
    n = tp.count_chars()
    items: list[TextItem] = []
    for i in range(n):
        try:
            ch = tp.get_text_range(i, 1)
        except Exception:
            continue
        if not ch or not ch.strip():
            continue
        try:
            x0, y0, x1, y1 = tp.get_charbox(i)
        except Exception:
            continue
        items.append(
            TextItem(
                x0=x0 * scale,
                y0=(height_pt - y1) * scale,
                x1=x1 * scale,
                y1=(height_pt - y0) * scale,
                text=ch,
            )
        )
    return items


def _median_height(items: list[TextItem]) -> float:
    hs = [i.h for i in items if i.h > 0.5]
    return statistics.median(hs) if hs else 10.0


# Dấu câu "dính" chữ: khoảng trắng chen vào hai bên sẽ sai nghĩa ("1, 2").
_TIGHT = set(",.;:!?，。、；：！？")


_NUMERIC = re.compile(r"^[\d\s,，]+$")


def _normalize_numbers(line: str) -> str:
    """Ô chỉ có số: thống nhất dấu phẩy (PDF lúc dùng ",", lúc dùng "，")."""
    if not _NUMERIC.fullmatch(line):
        return line
    return re.sub(r"\s*,\s*", ",", line.replace("，", ","))


def _join_line(items: list[TextItem], avg_h: float) -> str:
    items = sorted(items, key=lambda i: i.x0)
    parts: list[str] = []
    prev: TextItem | None = None
    for it in items:
        if prev is not None:
            gap = it.x0 - prev.x1
            if gap > 0.45 * avg_h:
                a, b = prev.text[-1:], it.text[:1]
                tight = a in _TIGHT or b in _TIGHT
                if not (tight or is_cjk(a) or is_cjk(b)):
                    parts.append(" ")
        parts.append(it.text)
        prev = it
    return "".join(parts).strip()


def _same_line(line: list[TextItem], it: TextItem, tol_bottom: float = 7.0) -> bool:
    """Cùng một dòng nếu hộp bao chồng lấn đáng kể theo trục dọc.

    So sánh độ chồng lấn thay vì khoảng cách tâm: chỉ số trên (10⁸) nằm cao
    hơn dòng, nên nếu so tâm nó bị tách thành dòng riêng và chữ trong ô bị đảo
    thứ tự — "隙)：1.51×10⁸" thành "隙1 51×108" + ")：.".

    Dấu phẩy/chấm là glyph thấp, gần như không chồng lấn với chữ số cùng dòng
    (ví dụ "1,2" bị tách thành "1 2" + ","), nên ngoài độ chồng lấn còn so
    thêm đường chân (đáy hộp bao) với dung sai `tol_bottom`.
    """
    y0 = min(i.y0 for i in line)
    y1 = max(i.y1 for i in line)
    inter = min(y1, it.y1) - max(y0, it.y0)
    if inter > 0 and inter >= 0.5 * min(y1 - y0, it.y1 - it.y0):
        return True
    return abs(it.y1 - y1) <= tol_bottom


def assemble_text(
    items: Iterable[TextItem],
    x0: float,
    y0: float,
    x1: float,
    y1: float,
    pad: float = 2.0,
) -> tuple[str, float | None]:
    """Ghép mọi item có tâm nằm trong hình chữ nhật thành text nhiều dòng."""
    inside = [
        i for i in items
        if x0 - pad <= i.cx <= x1 + pad and y0 - pad <= i.cy <= y1 + pad
    ]
    if not inside:
        return "", None

    avg_h = _median_height(inside)

    inside.sort(key=lambda i: (i.cy, i.x0))
    lines: list[list[TextItem]] = []
    cur: list[TextItem] = []
    tol_bottom = max(3.0, 0.35 * avg_h)
    for it in inside:
        if not cur or _same_line(cur, it, tol_bottom):
            cur.append(it)
        else:
            lines.append(cur)
            cur = [it]
    if cur:
        lines.append(cur)

    # sắp lại theo trục dọc (phòng khi item lệch)
    lines.sort(key=lambda ln: sum(i.cy for i in ln) / len(ln))
    text = "\n".join(_normalize_numbers(_join_line(ln, avg_h)) for ln in lines).strip()

    scores = [i.score for i in inside if i.score is not None]
    score = float(sum(scores) / len(scores)) if scores else None
    return text, score
