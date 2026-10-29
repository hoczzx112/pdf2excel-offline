"""Dò ô bảng bằng connected components của vùng trắng.

Ý tưởng: sau khi xoá vùng chữ, mỗi ô của bảng là một "đảo" trắng khép kín
bởi các đường kẻ. Nhờ vậy:
  * ô bị gộp (merge) tự nhiên có hộp bao lớn hơn — không cần heuristic
    "có viền hay không";
  * không bị ảnh hưởng bởi một đường kẻ nằm ở chỗ khác trên trang.
"""
from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from .gridlines import CellRect, _has_h_border, _has_v_border, remove_text_pixels

MIN_CELL_SIDE = 10          # px
MIN_FILL = 0.45             # tỉ lệ diện tích/diện tích hộp bao để coi là ô chữ nhật
LEAK_COVER = 0.10           # vùng lớn bị "rò rỉ" phải chứa ít nhất 10% diện tích là ô thật


@dataclass
class RawCell:
    x0: int
    y0: int
    x1: int
    y1: int


def find_cell_interiors(binary: np.ndarray) -> list[RawCell]:
    """Trả về hộp bao của các vùng trắng khép kín (nghi là ruột ô bảng)."""
    h, w = binary.shape[:2]
    white = np.where(binary > 0, 0, 1).astype(np.uint8)
    n, _labels, stats, _cent = cv2.connectedComponentsWithStats(white, connectivity=4)

    out: list[RawCell] = []
    for i in range(1, n):
        x, y, cw, ch, area = (
            int(stats[i, 0]), int(stats[i, 1]),
            int(stats[i, 2]), int(stats[i, 3]), int(stats[i, 4]),
        )
        if cw < MIN_CELL_SIDE or ch < MIN_CELL_SIDE:
            continue
        if area / float(cw * ch) < MIN_FILL:
            continue
        # bỏ vùng nền bao quanh cả trang
        touches = int(x <= 1) + int(y <= 1) + int(x + cw >= w - 1) + int(y + ch >= h - 1)
        if touches >= 3:
            continue
        if (cw * ch) > 0.90 * w * h:      # vùng nền gần kín trang
            continue
        out.append(RawCell(x, y, x + cw, y + ch))
    return out


def cluster_bounds(values: list[int], tol: int = 6) -> list[int]:
    centers, _counts = cluster_bounds_count(values, tol)
    return centers


def cluster_bounds_count(
    values: list[int], tol: int = 6
) -> tuple[list[int], list[int]]:
    """Gom toạ độ sát nhau; trả về (tâm cụm, số lần xuất hiện của cụm).

    Số lần xuất hiện dùng để loại biên "chỉ có một ô dùng" — thường là rác
    từ hình vẽ/logo chứ không phải vạch kẻ của bảng.
    """
    if not values:
        return [], []
    vals = sorted(values)
    centers: list[int] = []
    counts: list[int] = []
    cur = [vals[0]]
    for v in vals[1:]:
        if v - cur[-1] <= tol:
            cur.append(v)
        else:
            centers.append(int(round(sum(cur) / len(cur))))
            counts.append(len(cur))
            cur = [v]
    centers.append(int(round(sum(cur) / len(cur))))
    counts.append(len(cur))
    return centers, counts


def _line_masks(binary: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Tách nét kẻ ngang/dọc dài (bỏ nét chữ và cạnh ảnh)."""
    h, w = binary.shape[:2]
    k = max(20, int(round(min(h, w) * 0.025)))
    hl = cv2.morphologyEx(
        binary, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (k, 1))
    )
    vl = cv2.morphologyEx(
        binary, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (1, k))
    )
    return hl, vl


def _seal_line_gaps(
    binary: np.ndarray, hl: np.ndarray | None = None, vl: np.ndarray | None = None
) -> np.ndarray:
    """Vá các khe hở nhỏ trên đường kẻ (bản scan hay bị đứt 1–2 px).

    Chỉ tác động lên các vệt dài (đường kẻ), không làm dày nét chữ. Nếu không
    vá, vùng trắng "chảy" qua khe và nhiều ô bị nối thành một ô khổng lồ.
    """
    if hl is None or vl is None:
        hl, vl = _line_masks(binary)
    gap = 9
    hl = cv2.morphologyEx(hl, cv2.MORPH_CLOSE,
                          cv2.getStructuringElement(cv2.MORPH_RECT, (gap, 1)))
    vl = cv2.morphologyEx(vl, cv2.MORPH_CLOSE,
                          cv2.getStructuringElement(cv2.MORPH_RECT, (1, gap)))
    # Vá chỗ nối chữ T: đường kẻ ngang của PDF hay dừng cách đường dọc 1–2 px,
    # để lại một khe trắng đúng 1 px ở góc ô. Khe này không nằm trong mask
    # ngang hay dọc nên phép đóng ở trên không lấp được — hai ô trên/dưới bị
    # dính thành một (ví dụ ô "60s" nuốt luôn ô "300s").
    # Nới mỗi đường 1 px theo đúng phương của nó (không làm dày nét) là đủ.
    hl = cv2.dilate(hl, cv2.getStructuringElement(cv2.MORPH_RECT, (3, 1)))
    vl = cv2.dilate(vl, cv2.getStructuringElement(cv2.MORPH_RECT, (1, 3)))
    return cv2.bitwise_or(binary, cv2.bitwise_or(hl, vl))


def build_grid(
    cells: list[RawCell],
) -> tuple[list[int], list[int], list[tuple[int, int, int, int]]]:
    """Chuyển hộp bao ô -> chỉ số hàng/cột trong lưới chung của trang."""
    if not cells:
        return [], [], []

    # 1) Loại ô "rác" của hình vẽ/ảnh: cạnh không được ô nào khác chia sẻ và
    #    bản thân ô nhỏ. Ô thật luôn có ít nhất 2 cạnh trùng với ô bên cạnh
    #    (trừ ô ảnh lớn duy nhất — được giữ theo diện tích).
    row_c0, row_n0 = cluster_bounds_count([c.y0 for c in cells] + [c.y1 for c in cells])
    col_c0, col_n0 = cluster_bounds_count([c.x0 for c in cells] + [c.x1 for c in cells])

    def support(centers: list[int], counts: list[int], v: int) -> int:
        i = min(range(len(centers)), key=lambda k: abs(centers[k] - v))
        return counts[i]

    page_w = max(c.x1 for c in cells) or 1
    page_h = max(c.y1 for c in cells) or 1
    page_area = page_w * page_h
    min_area = 0.0002 * page_area      # mảnh vụn trong logo/ảnh
    min_big = 0.004 * page_area        # ô lớn: giữ dù ít cạnh chung
    valid: list[RawCell] = []
    for c in cells:
        area = (c.x1 - c.x0) * (c.y1 - c.y0)
        if area < min_area:
            continue
        rs = support(row_c0, row_n0, c.y0), support(row_c0, row_n0, c.y1)
        cs = support(col_c0, col_n0, c.x0), support(col_c0, col_n0, c.x1)
        shared = sum(1 for v in rs + cs if v >= 2)
        if shared >= 2 or area >= min_big:
            valid.append(c)
    if len(valid) >= 2:
        cells = valid

    row_c, row_n = cluster_bounds_count([c.y0 for c in cells] + [c.y1 for c in cells])
    col_c, col_n = cluster_bounds_count([c.x0 for c in cells] + [c.x1 for c in cells])
    if len(row_c) < 2 or len(col_c) < 2:
        return row_c, col_c, []

    def idx_of(centers: list[int], v: int) -> int:
        return min(range(len(centers)), key=lambda i: abs(centers[i] - v))

    idx: list[tuple[int, int, int, int]] = []
    for c in cells:
        r0 = idx_of(row_c, c.y0)
        r1 = idx_of(row_c, c.y1)
        c0 = idx_of(col_c, c.x0)
        c1 = idx_of(col_c, c.x1)
        if r1 > r0 and c1 > c0:
            idx.append((r0, c0, r1, c1))
    return row_c, col_c, idx




def _prune_nested(cells: list[RawCell]) -> list[RawCell]:
    """Xử lý các ô lồng nhau.

    Hai trường hợp hoàn toàn khác nhau:
      * Ô lớn chứa **nhiều ô thật** (>=2 ô, phủ >=10% diện tích) → ô lớn là
        vùng bị "rò rỉ" do đường kẻ đứt; bỏ ô lớn, giữ các ô thật.
      * Ô lớn chỉ chứa **mảnh vụn** của hình vẽ/logo → giữ ô lớn, bỏ mảnh.
    """
    def area(c: RawCell) -> int:
        return max(1, (c.x1 - c.x0) * (c.y1 - c.y0))

    ordered = sorted(cells, key=area, reverse=True)
    drop: set[int] = set()
    for i, big in enumerate(ordered):
        if i in drop:
            continue
        inside: list[int] = []
        for j in range(i + 1, len(ordered)):
            if j in drop:
                continue
            c = ordered[j]
            ix = min(c.x1, big.x1) - max(c.x0, big.x0)
            iy = min(c.y1, big.y1) - max(c.y0, big.y0)
            if ix > 2 and iy > 2 and (ix * iy) / area(c) > 0.90:
                inside.append(j)
        if not inside:
            continue
        cover = sum(area(ordered[j]) for j in inside) / area(big)
        if len(inside) >= 2 and cover >= LEAK_COVER:
            drop.add(i)                 # ô lớn = vùng rò rỉ
        else:
            drop.update(inside)         # mảnh vụn nằm trong ô lớn
    return [c for k, c in enumerate(ordered) if k not in drop]


def _fill_grid_holes(
    rects: list[CellRect],
    rows: list[int],
    cols: list[int],
    hl: np.ndarray,
    vl: np.ndarray,
    items,
) -> list[CellRect]:
    """Bổ sung ô bị bỏ sót nhưng có chữ và đủ 4 cạnh là nét kẻ thật.

    Vùng trắng của ô chứa ảnh/chữ lớn có thể "rò" sang ô bên cạnh, khiến ô đó
    biến mất khỏi kết quả (và chữ trong ô bị mất theo). Ở đây ta dựa vào chính
    lưới nét kẻ để dựng lại những ô như vậy. Chỉ nhận ô **có chữ**, nên không
    kéo theo các "ô" chỉ chứa ảnh (vốn không xuất được sang Excel).
    """
    if not rects or items is None or len(rows) < 2 or len(cols) < 2:
        return rects
    nr, nc = len(rows) - 1, len(cols) - 1
    used = np.zeros((nr, nc), dtype=bool)
    for r in rects:
        used[r.r0: r.r1 + 1, r.c0: r.c1 + 1] = True

    # Giới hạn kích thước: ô lấp thêm chỉ là ô bị bỏ sót, không phải cả vùng
    # bản vẽ. Nhờ vậy không tái sinh "ô khổng lồ".
    span_h = rows[-1] - rows[0] or 1
    span_w = cols[-1] - cols[0] or 1
    row_step = float(np.median(np.diff(rows))) if len(rows) > 2 else span_h
    col_step = float(np.median(np.diff(cols))) if len(cols) > 2 else span_w
    max_h = max(0.30 * span_h, 4 * row_step)
    max_w = max(0.80 * span_w, 4 * col_step)

    def has_text(x0: int, y0: int, x1: int, y1: int) -> bool:
        return any(x0 <= it.cx <= x1 and y0 <= it.cy <= y1 for it in items)

    def adjacent(i0: int, i1: int, j0: int, j1: int) -> bool:
        """Ô mới phải dính vào một ô đã dò (tránh dựng ô giữa vùng trống)."""
        if i0 > 0 and used[i0 - 1, j0: j1 + 1].any():
            return True
        if i1 + 1 < nr and used[i1 + 1, j0: j1 + 1].any():
            return True
        if j0 > 0 and used[i0: i1 + 1, j0 - 1].any():
            return True
        if j1 + 1 < nc and used[i0: i1 + 1, j1 + 1].any():
            return True
        return False

    added: list[CellRect] = []
    for i in range(nr):
        for j in range(nc):
            if used[i, j]:
                continue
            jj = j
            while (jj + 1 < nc and not used[i, jj + 1]
                   and not _has_v_border(vl, cols[jj + 1], rows[i], rows[i + 1])):
                jj += 1
            i_down = i
            while i_down + 1 < nr and all(
                not used[i_down + 1, c]
                and not _has_h_border(hl, rows[i_down + 1], cols[c], cols[c + 1])
                for c in range(j, jj + 1)
            ):
                i_down += 1
            # mở rộng lên trên: hàng tiêu đề nằm sát bảng phía trên
            i_up = i
            while i_up > 0 and all(
                not used[i_up - 1, c]
                and not _has_h_border(hl, rows[i_up], cols[c], cols[c + 1])
                for c in range(j, jj + 1)
            ):
                i_up -= 1
            x0, y0, x1, y1 = cols[j], rows[i_up], cols[jj + 1], rows[i_down + 1]
            if y1 - y0 > max_h or x1 - x0 > max_w:
                continue
            if not adjacent(i_up, i_down, j, jj):
                continue
            if not has_text(x0, y0, x1, y1):
                continue
            ok = (
                _has_v_border(vl, x0, y0, y1)
                and _has_v_border(vl, x1, y0, y1)
                and _has_h_border(hl, y0, x0, x1)
                and _has_h_border(hl, y1, x0, x1)
            )
            if not ok:
                continue
            used[i_up: i_down + 1, j: jj + 1] = True
            added.append(CellRect(i_up, j, i_down, jj, x0, y0, x1, y1))
    return rects + added


def page_cell_rects(binary: np.ndarray, items, remove_text: bool = True) -> list[CellRect]:
    """Toàn bộ quy trình: ảnh nhị phân + vùng chữ -> danh sách ô hình chữ nhật.

    remove_text=True dùng cho text layer (hộp bao từng ký tự, rất chính xác).
    Với OCR, hộp bao là cả dòng chữ nên xoá sẽ làm đứt vạch kẻ giữa các ô —
    khi đó để nguyên chữ, chỉ dựa vào tỉ lệ lấp đầy của vùng trắng.
    """
    clean = remove_text_pixels(binary, items) if remove_text else binary
    hl, vl = _line_masks(clean)
    sealed = _seal_line_gaps(clean, hl, vl)
    interiors = _prune_nested(find_cell_interiors(sealed))
    rows, cols, idxs = build_grid(interiors)
    rects = [
        CellRect(r0, c0, r1 - 1, c1 - 1, cols[c0], rows[r0], cols[c1], rows[r1])
        for (r0, c0, r1, c1) in idxs
    ]
    return _fill_grid_holes(rects, rows, cols, hl, vl, items)
