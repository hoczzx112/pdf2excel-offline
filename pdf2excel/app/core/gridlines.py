"""Dò lưới kẻ của bảng từ ảnh trang, gộp ô, và chia thành từng bảng riêng.

Thuật toán:
1. Nhị phân hoá ảnh (đường kẻ + chữ -> 255).
2. Tách đường ngang bằng morphological opening với kernel ngang dài
   (~2% chiều rộng) -> chỉ giữ các "vệt" đủ dài, loại nét chữ.
   Tương tự cho đường dọc.
3. Gom toạ độ các đường thành danh sách hàng/cột.
4. Với từng ô lưới, kiểm tra 4 cạnh có đường kẻ hay không (đo tỉ lệ pixel
   tối trên dải mỏng quanh cạnh) -> union-find để gộp các ô không có vạch
   ngăn (ô merge).
5. Gom các ô rời rạc thành từng bảng dựa trên khoảng cách hình học.
"""
from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from ..config import (
    BORDER_PRESENCE,
    LINE_GROUP_GAP,
    MIN_TABLE_CELLS,
    TABLE_JOIN_GAP,
)


class _DSU:
    def __init__(self, n: int) -> None:
        self.p = list(range(n))

    def find(self, a: int) -> int:
        while self.p[a] != a:
            self.p[a] = self.p[self.p[a]]
            a = self.p[a]
        return a

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[rb] = ra


@dataclass
class CellRect:
    """Một ô thật (sau khi gộp), toạ độ pixel."""

    r0: int
    c0: int
    r1: int
    c1: int
    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def rows(self) -> int:
        return self.r1 - self.r0 + 1

    @property
    def cols(self) -> int:
        return self.c1 - self.c0 + 1


def to_binary(gray: np.ndarray) -> np.ndarray:
    """Ảnh xám -> mask 0/255 với nét (kẻ + chữ) = 255."""
    if gray.ndim == 3:
        gray = cv2.cvtColor(gray, cv2.COLOR_RGB2GRAY)
    inv = 255 - gray
    bw = cv2.adaptiveThreshold(
        inv, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY, 15, -2
    )
    return bw


def remove_text_pixels(binary: np.ndarray, items, inset: int = 1) -> np.ndarray:
    """Xoá vùng chữ khỏi mask để chỉ còn đường kẻ.

    Ta biết chính xác hộp bao của từng ký tự (text layer) hoặc từng dòng chữ
    (OCR), nên có thể loại nét chữ trước khi dò đường kẻ. Nhờ vậy không cần
    phân biệt "nét chữ thẳng đứng" với "đường kẻ ô" nữa — vốn rất dễ nhầm
    với chữ cỡ lớn hoặc ô có hàng hẹp.

    Hộp bao ký tự của pdfium thường chạm sát đường kẻ ô. Nếu xoá trọn hộp
    (nhất là nới thêm 1 px) thì đường kẻ bị "gặm" mất 1–2 px ngay chỗ chữ nằm
    sát viền; vùng trắng khi đó rò sang ô bên cạnh và hai ô bị gộp làm một.
    Vì vậy hộp xoá được thụt vào `inset` px thay vì nới ra.
    """
    out = binary.copy()
    h, w = out.shape[:2]
    for it in items:
        x0 = max(0, int(round(it.x0)) + inset)
        y0 = max(0, int(round(it.y0)) + inset)
        x1 = min(w, int(round(it.x1)) - inset + 1)
        y1 = min(h, int(round(it.y1)) - inset + 1)
        if x1 > x0 and y1 > y0:
            out[y0:y1, x0:x1] = 0
    return out


def _group(coords: np.ndarray, gap: int = LINE_GROUP_GAP) -> list[int]:
    if coords.size == 0:
        return []
    out: list[int] = []
    cur = [int(coords[0])]
    for v in coords[1:]:
        v = int(v)
        if v - cur[-1] <= gap:
            cur.append(v)
        else:
            out.append(int(round(sum(cur) / len(cur))))
            cur = [v]
    out.append(int(round(sum(cur) / len(cur))))
    return out


def detect_grid_lines(
    binary: np.ndarray, min_ratio: float = 0.006
) -> tuple[np.ndarray, np.ndarray, list[int], list[int]]:
    """Trả về (hmask, vmask, rows, cols).

    `binary` phải là ảnh đã xoá vùng chữ (xem remove_text_pixels), khi đó
    không còn nét chữ nên có thể dùng kernel ngắn để bắt cả các ô nhỏ.
    """
    h, w = binary.shape[:2]
    klen_h = max(14, int(w * min_ratio))
    klen_v = max(14, int(h * min_ratio))

    hopen = cv2.morphologyEx(
        binary, cv2.MORPH_OPEN,
        cv2.getStructuringElement(cv2.MORPH_RECT, (klen_h, 1)),
    )
    vopen = cv2.morphologyEx(
        binary, cv2.MORPH_OPEN,
        cv2.getStructuringElement(cv2.MORPH_RECT, (1, klen_v)),
    )

    min_row_cov = max(klen_h * 1.4, w * 0.004)
    min_col_cov = max(klen_v * 1.4, h * 0.004)

    rows = _group(np.flatnonzero((hopen > 0).sum(axis=1) >= min_row_cov))
    cols = _group(np.flatnonzero((vopen > 0).sum(axis=0) >= min_col_cov))

    if len(rows) > 140:
        rows = rows[:140]
    if len(cols) > 140:
        cols = cols[:140]

    hmask = np.zeros_like(binary)
    for y in rows:
        a, b = max(0, y - 2), min(h, y + 3)
        hmask[a:b, :] = binary[a:b, :]
    vmask = np.zeros_like(binary)
    for x in cols:
        a, b = max(0, x - 2), min(w, x + 3)
        vmask[:, a:b] = binary[:, a:b]

    return hmask, vmask, rows, cols


def _edge_ok(signal: np.ndarray, a: int, b: int) -> bool:
    """Cạnh ô có đường kẻ hay không (dựa trên độ phủ + độ dài đoạn liền)."""
    a = max(0, int(a))
    b = min(len(signal), int(b))
    span = b - a
    if span < 4:
        return True
    seg = signal[a:b] > 0
    fill = float(seg.mean())
    if fill == 0.0:
        return False
    d = np.diff(np.concatenate(([0], seg.astype(np.int8), [0])))
    starts = np.flatnonzero(d == 1)
    ends = np.flatnonzero(d == -1)
    longest = float((ends - starts).max())
    return fill >= BORDER_PRESENCE and longest >= 0.25 * span


def _has_h_border(hmask: np.ndarray, y: int, x0: float, x1: float) -> bool:
    h = hmask.shape[0]
    strip = hmask[max(0, y - 2): min(h, y + 3), :]
    if strip.size == 0:
        return True
    return _edge_ok(strip.max(axis=0), int(round(x0)) + 2, int(round(x1)) - 2)


def _has_v_border(vmask: np.ndarray, x: int, y0: float, y1: float) -> bool:
    w = vmask.shape[1]
    strip = vmask[:, max(0, x - 2): min(w, x + 3)]
    if strip.size == 0:
        return True
    return _edge_ok(strip.max(axis=1), int(round(y0)) + 2, int(round(y1)) - 2)


def find_cell_rects(
    hmask: np.ndarray, vmask: np.ndarray, rows: list[int], cols: list[int]
) -> list[CellRect]:
    """Dựng lưới và gộp các ô không có vạch ngăn."""
    nr, nc = len(rows) - 1, len(cols) - 1
    if nr < 1 or nc < 1:
        return []

    # kiểm tra cạnh phải / cạnh dưới của từng ô lưới
    right = np.zeros((nr, nc), dtype=bool)   # có vạch dọc bên phải
    bottom = np.zeros((nr, nc), dtype=bool)  # có vạch ngang bên dưới
    for i in range(nr):
        y0, y1 = rows[i], rows[i + 1]
        for j in range(nc):
            x0, x1 = cols[j], cols[j + 1]
            right[i, j] = _has_v_border(vmask, cols[j + 1], y0, y1)
            bottom[i, j] = _has_h_border(hmask, rows[i + 1], x0, x1)

    # Lan toả theo hình chữ nhật (greedy). Cách này đảm bảo mỗi ô luôn là
    # hình chữ nhật — nếu chỉ dùng union-find, một nhóm ô hình chữ L sẽ có
    # hộp bao trùm lên ô của nhóm khác và chữ bị nhân bản/mất.
    used = np.zeros((nr, nc), dtype=bool)
    rects: list[CellRect] = []
    for i in range(nr):
        for j in range(nc):
            if used[i, j]:
                continue
            # mở rộng sang phải
            c1 = j
            while c1 < nc - 1 and not right[i, c1] and not used[i, c1 + 1]:
                c1 += 1
            # mở rộng xuống dưới
            r1 = i
            while r1 < nr - 1:
                if any(bottom[r1, c] for c in range(j, c1 + 1)):
                    break
                if any(used[r1 + 1, c] for c in range(j, c1 + 1)):
                    break
                if any(right[r1 + 1, c] for c in range(j, c1)):
                    break
                r1 += 1
            used[i: r1 + 1, j: c1 + 1] = True
            rects.append(
                CellRect(i, j, r1, c1, cols[j], rows[i], cols[c1 + 1], rows[r1 + 1])
            )
    return rects


def _overlaps_inflated(a: CellRect, b: CellRect, gap: float) -> bool:
    return not (
        a.x1 + gap < b.x0
        or b.x1 + gap < a.x0
        or a.y1 + gap < b.y0
        or b.y1 + gap < a.y0
    )


def group_tables(rects: list[CellRect], page_h: float) -> list[list[CellRect]]:
    """Gom các ô thành bảng dựa trên khoảng cách hình học."""
    if not rects:
        return []
    gap = max(6.0, TABLE_JOIN_GAP * page_h)
    dsu = _DSU(len(rects))
    for i in range(len(rects)):
        for j in range(i + 1, len(rects)):
            if _overlaps_inflated(rects[i], rects[j], gap):
                dsu.union(i, j)

    groups: dict[int, list[CellRect]] = {}
    for i, r in enumerate(rects):
        groups.setdefault(dsu.find(i), []).append(r)

    out = []
    for g in groups.values():
        if len(g) >= MIN_TABLE_CELLS:
            out.append(g)
    # bảng ở trên trước
    out.sort(key=lambda g: (min(r.y0 for r in g), min(r.x0 for r in g)))
    return out
