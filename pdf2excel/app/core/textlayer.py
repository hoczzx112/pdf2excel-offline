"""Trích chữ từ text layer của PDF và ghép chữ vào từng ô bảng.

Dùng chung cho cả hai nhánh: text layer (toạ độ ký tự từ pdfium) và OCR
(toạ độ dòng chữ từ RapidOCR). Nhờ vậy phần dựng bảng chỉ có một cài đặt.
"""
from __future__ import annotations

import re
import statistics
from collections.abc import Iterable
from dataclasses import dataclass

from .models import TextItem

try:                                  # cần cho FPDFText_GetCharOrigin
    import ctypes as _ctypes

    import pypdfium2.raw as _pdfium_raw
except Exception:                     # thiếu bản build -> bỏ qua bước chữ dọc
    _ctypes = None
    _pdfium_raw = None

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


def _page_geometry(page) -> tuple[int, float, float, float, float]:
    """(rotate, x_off, y_off, w0, h0) của trang *chưa* xoay, đơn vị point.

    pdfium trả hộp bao ký tự trong hệ toạ độ gốc của trang (gốc dưới trái, y
    hướng lên, chưa áp dụng /Rotate), còn ảnh render lại đã xoay theo /Rotate.
    """
    try:
        rot = int(page.get_rotation()) % 360
    except Exception:
        rot = 0
    try:
        left, bottom, right, top = page.get_mediabox()
        w0, h0 = right - left, top - bottom
    except Exception:
        w, h = page.get_size()
        left = bottom = 0.0
        w0, h0 = (h, w) if rot in (90, 270) else (w, h)
    if w0 <= 0 or h0 <= 0:            # mediabox hỏng -> đoán theo /Rotate
        w, h = page.get_size()
        left = bottom = 0.0
        w0, h0 = (h, w) if rot in (90, 270) else (w, h)
    return rot, left, bottom, w0, h0


def _char_origin(tp, index: int) -> tuple[float, float] | None:
    """Gốc của ký tự trong hệ toạ độ gốc của trang (point, y hướng lên)."""
    if _pdfium_raw is None:
        return None
    try:
        x = _ctypes.c_double()
        y = _ctypes.c_double()
        ok = _pdfium_raw.FPDFText_GetCharOrigin(
            tp.raw, index, _ctypes.byref(x), _ctypes.byref(y)
        )
        return (x.value, y.value) if ok else None
    except Exception:
        return None


@dataclass
class _Entry:
    index: int
    text: str
    origin: tuple[float, float] | None
    box: tuple[float, float, float, float]


def _reslice_runs(entries: list[_Entry], tol: float = 0.6) -> None:
    """Chia lại hộp bao cho dòng chữ mà pdfium cắt thành lát mỏng.

    Có những dòng (thường là chữ xoay trong bản vẽ kỹ thuật) mà pdfium trả
    *cùng một gốc* cho mọi ký tự và cắt hộp bao thành những lát mỏng vuông
    góc với chiều chạy của dòng, mỗi lát lại kéo dài hết cả dòng. Hệ quả: mỗi
    ký tự nhận hộp bao của cả dòng, nên khi ghép chữ trong ô, mỗi ký tự bị
    xếp vào một "dòng" riêng — "项目分类" thành "项\\n目\\n分\\n类".
    Gom các ký tự cùng gốc rồi cắt lại dòng thành từng đoạn bằng nhau.

    Chỉ xử lý dòng nằm ngang trên ảnh (sau khi đã áp dụng /Rotate). Dòng chạy
    dọc để nguyên: các lát khi đó nằm cạnh nhau theo trục x nên vẫn ghép ra
    đúng thứ tự, còn nếu cắt lại theo trục y thì mỗi ký tự thành một dòng
    riêng trong Excel — sai hơn nhiều.
    """
    groups: dict[tuple[int, int], list[_Entry]] = {}
    for e in entries:
        if e.origin is None:
            continue
        key = (round(e.origin[0] / tol), round(e.origin[1] / tol))
        groups.setdefault(key, []).append(e)

    for group in groups.values():
        if len(group) < 2:
            continue
        x0 = min(e.box[0] for e in group)
        y0 = min(e.box[1] for e in group)
        x1 = max(e.box[2] for e in group)
        y1 = max(e.box[3] for e in group)
        w, h = x1 - x0, y1 - y0
        if w <= 0 or h <= 0 or w < h:
            continue
        # chỉ cắt lại khi các hộp bao đúng là lát mỏng xếp chồng theo trục dọc
        if not all((e.box[3] - e.box[1]) <= 0.6 * h for e in group):
            continue
        n = len(group)
        order = sorted(group, key=lambda e: e.box[1] + e.box[3])
        step = w / n
        for k, e in enumerate(order):
            e.box = (x0 + k * step, y0, x0 + (k + 1) * step, y1)


def _overlap_ratio(a, b) -> float:
    ix = min(a[2], b[2]) - max(a[0], b[0])
    iy = min(a[3], b[3]) - max(a[1], b[1])
    if ix <= 0 or iy <= 0:
        return 0.0
    small = min((a[2] - a[0]) * (a[3] - a[1]), (b[2] - b[0]) * (b[3] - b[1]))
    return (ix * iy) / small if small > 0 else 0.0


def _drop_covered(entries: list[_Entry], ratio: float = 0.6) -> None:
    """Bỏ lớp chữ cũ bị vẽ đè (file PDF sửa tay để lại chữ cũ bên dưới).

    Ví dụ trang 2 của JYCP: ô "页码" có cả "共7页第2页" (vẽ sau) lẫn "共2页第7页"
    (vẽ trước, bị che). Giữ bản vẽ sau vì đó là bản nhìn thấy trên giấy.
    """
    n = len(entries)
    if n < 2:
        return
    alive = [True] * n
    order = sorted(range(n), key=lambda i: entries[i].box[1])
    for a_pos, i in enumerate(order):
        if not alive[i]:
            continue
        bi = entries[i].box
        for j in order[a_pos + 1:]:
            bj = entries[j].box
            if bj[1] >= bi[3]:
                break
            if not alive[j] or entries[j].text == entries[i].text:
                continue
            if _overlap_ratio(bi, bj) > ratio:
                if entries[j].index > entries[i].index:
                    alive[i] = False
                    break
                alive[j] = False
    entries[:] = [e for k, e in enumerate(entries) if alive[k]]


def page_text_items(page, scale: float) -> list[TextItem]:
    """Lấy từng ký tự của text layer kèm toạ độ pixel (y hướng xuống).

    Phải xoay hộp bao theo /Rotate của trang. Trang nằm ngang nhưng lưu dưới
    dạng dọc (/Rotate 90) nếu bỏ qua bước này sẽ ra toạ độ âm, chữ bị ghép
    vào sai ô và cả bảng thành rác.
    """
    rot, off_x, off_y, w0, h0 = _page_geometry(page)
    tp = page.get_textpage()
    n = tp.count_chars()
    entries: list[_Entry] = []
    for i in range(n):
        try:
            ch = tp.get_text_range(i, 1)
        except Exception:
            continue
        if not ch or not ch.strip():
            continue
        try:
            left, bottom, right, top = tp.get_charbox(i)
        except Exception:
            continue
        origin = _char_origin(tp, i)
        left -= off_x
        right -= off_x
        bottom -= off_y
        top -= off_y

        if rot == 90:                 # xoay 90° theo chiều kim đồng hồ
            x0, y0, x1, y1 = bottom, left, top, right
        elif rot == 180:
            x0, y0, x1, y1 = w0 - right, h0 - top, w0 - left, h0 - bottom
        elif rot == 270:
            x0, y0, x1, y1 = h0 - top, w0 - right, h0 - bottom, w0 - left
        else:
            x0, y0, x1, y1 = left, h0 - top, right, h0 - bottom

        entries.append(
            _Entry(
                index=i,
                text=ch,
                origin=origin,
                box=(x0 * scale, y0 * scale, x1 * scale, y1 * scale),
            )
        )

    _reslice_runs(entries)
    _drop_covered(entries)
    return [
        TextItem(x0=e.box[0], y0=e.box[1], x1=e.box[2], y1=e.box[3], text=e.text)
        for e in entries
    ]


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
