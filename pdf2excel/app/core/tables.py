"""Dựng bảng (grid) từ các ô đã dò được + chữ trong ô."""
from __future__ import annotations

import statistics

from ..config import MIN_TABLE_CELLS
from .gridlines import CellRect, group_tables
from .models import Table, TableCell, TextItem
from .textlayer import _same_line, assemble_text, is_cjk

# Ký hiệu rộng đúng bằng một chữ Hán nhưng không nằm trong dải CJK
# (east_asian_width = "A" nên dễ bị coi là nửa độ rộng).
_WIDE_SYMBOLS = set("★☆●○◎◆◇■□▲△▼▽※⊕⊗")


def _char_units(ch: str) -> float:
    """Độ rộng tương đối của ký tự (đơn vị = nửa độ rộng chữ Hán)."""
    if ch in ".．,，":
        return 0.45
    if ch in "-−~–":
        return 0.55
    if ch == " ":
        return 0.5
    if ch in _WIDE_SYMBOLS or is_cjk(ch):
        return 2.0
    return 1.0


def _line_coords(
    lines: list[int], known: dict[int, float]
) -> list[float] | None:
    """Đổi chỉ số đường kẻ -> toạ độ pixel (nội suy mốc thiếu nếu có)."""
    if len(lines) < 2:
        return None
    out: list[float] = []
    prev: int | None = None
    for idx in lines:
        if idx in known:
            out.append(float(known[idx]))
            prev = idx
            continue
        nxt = next((j for j in lines if j > idx and j in known), None)
        if prev is not None and nxt is not None:
            x0, x1 = known[prev], known[nxt]
            out.append(x0 + (x1 - x0) * (idx - prev) / (nxt - prev))
        elif prev is not None:
            out.append(float(known[prev]))
        elif nxt is not None:
            out.append(float(known[nxt]))
        else:
            return None
    return out


def _crop_edges(
    cells: list[TableCell], num_rows: int, num_cols: int
) -> tuple[list[TableCell], int, int, int, int] | None:
    """Cắt các hàng/cột rỗng ở rìa bảng (thường do viền trang bị nhận nhầm).

    Trả thêm (r_off, c_off) để cắt cả mảng kích thước hàng/cột tương ứng.
    """
    filled = [[False] * num_cols for _ in range(num_rows)]
    for c in cells:
        if not c.text.strip():
            continue
        for r in range(c.row, min(num_rows, c.row + c.rowspan)):
            for cc in range(c.col, min(num_cols, c.col + c.colspan)):
                filled[r][cc] = True

    rows_f = [i for i in range(num_rows) if any(filled[i])]
    cols_f = [j for j in range(num_cols) if any(filled[i][j] for i in range(num_rows))]
    if not rows_f or not cols_f:
        return None
    r0, r1 = rows_f[0], rows_f[-1]
    c0, c1 = cols_f[0], cols_f[-1]
    if r0 == 0 and c0 == 0 and r1 == num_rows - 1 and c1 == num_cols - 1:
        return cells, num_rows, num_cols, 0, 0

    out: list[TableCell] = []
    for c in cells:
        nr0 = max(0, c.row - r0)
        nc0 = max(0, c.col - c0)
        nr1 = min(r1 - r0 + 1, c.row + c.rowspan - r0)
        nc1 = min(c1 - c0 + 1, c.col + c.colspan - c0)
        if nr1 <= nr0 or nc1 <= nc0:
            continue
        out.append(
            TableCell(
                row=nr0, col=nc0,
                rowspan=nr1 - nr0, colspan=nc1 - nc0,
                text=c.text, score=c.score,
            )
        )
    return out, r1 - r0 + 1, c1 - c0 + 1, r0, c0


def _estimate_line_height(items: list[TextItem], default: float = 12.0) -> float:
    hs = [i.h for i in items if i.h > 0.5]
    return statistics.median(hs) if hs else default


def _cell_borders(group: list[CellRect]) -> list[tuple[float, float, float]]:
    """Biên dọc **thật** giữa hai ô kề nhau: danh sách (x, y_trên, y_dưới).

    Chỉ lấy biên của cặp ô có thật trong lưới, không lấy mọi đường kẻ dọc của
    bảng: ở hàng có ô gộp, nhiều đường kẻ của lưới không hề tồn tại.
    """
    by_left: dict[int, list[CellRect]] = {}
    for b in group:
        by_left.setdefault(round(b.x0), []).append(b)
    found: dict[tuple[int, int, int], tuple[float, float, float]] = {}
    for a in group:
        k = round(a.x1)
        for key in range(k - 2, k + 3):
            for b in by_left.get(key, ()):
                if abs(a.x1 - b.x0) > 1.5:
                    continue
                top = max(a.y0, b.y0)
                bot = min(a.y1, b.y1)
                if bot - top < 0.6 * min(a.y1 - a.y0, b.y1 - b.y0):
                    continue
                x = (a.x1 + b.x0) / 2.0
                found[(round(x), round(top), round(bot))] = (x, top, bot)
    return list(found.values())


def _cut_item(it: TextItem, cuts: list[float]) -> list[TextItem] | None:
    """Cắt một item OCR thành nhiều item theo các biên ô (toạ độ x)."""
    text = it.text
    units = [_char_units(c) for c in text]
    total = sum(units)
    if len(text) < 2 or total <= 0 or it.w <= 0:
        return None
    unit = it.w / total                   # px cho 1 đơn vị độ rộng

    picked: list[tuple[float, int]] = []
    last = 0
    for x in cuts:
        rel = (x - it.x0) / unit          # vị trí biên theo đơn vị ký tự
        acc = 0.0
        best, best_d = 0, 1e9
        for k in range(1, len(text)):
            acc += units[k - 1]
            if abs(acc - rel) < best_d:
                best, best_d = k, abs(acc - rel)
        if best <= last or best >= len(text):
            continue
        if best_d > 0.6:                   # biên rơi giữa ký tự -> không cắt
            continue
        left_units = sum(units[:best])
        right_units = total - left_units
        if left_units < 0.8 or right_units < 0.8:
            continue
        picked.append((x, best))
        last = best
    if not picked:
        return None

    edges = [it.x0] + [x for x, _ in picked] + [it.x1]
    idxs = [0] + [k for _, k in picked] + [len(text)]
    out: list[TextItem] = []
    for i in range(len(picked) + 1):
        part = text[idxs[i]:idxs[i + 1]].strip()
        if not part:
            continue
        out.append(
            TextItem(
                x0=edges[i], y0=it.y0, x1=edges[i + 1], y1=it.y1,
                text=part, score=it.score,
            )
        )
    return out or None


def _split_crossing_items(
    items: list[TextItem], group: list[CellRect]
) -> list[TextItem]:
    """Tách item OCR tràn qua đường kẻ giữa hai ô thật.

    RapidOCR hay gộp hai ô cạnh nhau thành một dòng chữ (nhất là khi ô bên trái
    có chữ sát đường kẻ). Nếu để nguyên, `assemble_text` xếp cả dòng vào ô chứa
    tâm item: ô bên trái nhận cả chữ của ô bên phải, còn ô bên phải trống.
    """
    borders = _cell_borders(group)
    if not borders:
        return items
    out: list[TextItem] = []
    for it in items:
        cuts = sorted(
            x for x, top, bot in borders
            if it.x0 + 2.0 < x < it.x1 - 2.0 and top - 2.0 <= it.cy <= bot + 2.0
        )
        parts = _cut_item(it, cuts) if cuts else None
        if parts:
            out.extend(parts)
        else:
            out.append(it)
    return out


def build_page_tables(
    rects: list[CellRect],
    items: list[TextItem],
    page_no: int,
    engine: str,
    page_h: float,
    px_per_pt: float = 1.0,
) -> list[Table]:
    """Gom ô -> bảng, ghép chữ vào từng ô, chuẩn hoá chỉ số hàng/cột.

    px_per_pt: số pixel ảnh ứng với 1 point của PDF (thường = DPI / 72); dùng để
    ghi lại kích thước thật của cột/hàng cho bước xuất Excel.
    """
    scale = max(1e-6, float(px_per_pt))
    tables: list[Table] = []
    for idx, group in enumerate(group_tables(rects, page_h), start=1):
        group_items = _split_crossing_items(items, group)
        row_lines = sorted({r.r0 for r in group} | {r.r1 + 1 for r in group})
        col_lines = sorted({r.c0 for r in group} | {r.c1 + 1 for r in group})
        row_map = {v: i for i, v in enumerate(row_lines)}
        col_map = {v: i for i, v in enumerate(col_lines)}
        num_rows = len(row_lines) - 1
        num_cols = len(col_lines) - 1
        if num_rows < 1 or num_cols < 1 or num_rows * num_cols > 20000:
            continue

        col_pt: list[float] = []
        row_pt: list[float] = []
        col_coord: dict[int, float] = {}
        row_coord: dict[int, float] = {}
        for r in group:
            col_coord.setdefault(r.c0, r.x0)
            col_coord.setdefault(r.c1 + 1, r.x1)
            row_coord.setdefault(r.r0, r.y0)
            row_coord.setdefault(r.r1 + 1, r.y1)
        col_px = _line_coords(col_lines, col_coord)
        row_px = _line_coords(row_lines, row_coord)
        col_edges: list[float] = []
        row_edges: list[float] = []
        if col_px and len(col_px) == num_cols + 1:
            col_edges = [v / scale for v in col_px]
            col_pt = [col_edges[i + 1] - col_edges[i] for i in range(num_cols)]
        if row_px and len(row_px) == num_rows + 1:
            row_edges = [v / scale for v in row_px]
            row_pt = [row_edges[i + 1] - row_edges[i] for i in range(num_rows)]

        cells: list[TableCell] = []
        for r in group:
            text, score = assemble_text(group_items, r.x0, r.y0, r.x1, r.y1)
            cells.append(
                TableCell(
                    row=row_map[r.r0],
                    col=col_map[r.c0],
                    rowspan=row_map[r.r1 + 1] - row_map[r.r0],
                    colspan=col_map[r.c1 + 1] - col_map[r.c0],
                    text=text,
                    score=score,
                )
            )

        if not any(c.text.strip() for c in cells):
            continue

        cropped = _crop_edges(cells, num_rows, num_cols)
        if cropped is None:
            continue
        cells, num_rows, num_cols, r_off, c_off = cropped
        col_pt = col_pt[c_off:c_off + num_cols]
        row_pt = row_pt[r_off:r_off + num_rows]
        col_edges = col_edges[c_off:c_off + num_cols + 1]
        row_edges = row_edges[r_off:r_off + num_rows + 1]

        x0 = min(r.x0 for r in group)
        y0 = min(r.y0 for r in group)
        x1 = max(r.x1 for r in group)
        y1 = max(r.y1 for r in group)
        tables.append(
            Table(
                page=page_no,
                index=len(tables) + 1,
                num_rows=num_rows,
                num_cols=num_cols,
                cells=cells,
                engine=engine,
                bbox=(x0, y0, x1, y1),
                col_widths=col_pt,
                row_heights=row_pt,
                col_edges=col_edges,
                row_edges=row_edges,
            )
        )
    return tables


def _cluster_edges(values: list[float], tol: float = 0.6) -> list[float]:
    """Gộp các đường kẻ gần trùng nhau (sai số làm tròn) thành một mốc."""
    out: list[float] = []
    for v in sorted(values):
        if out and v - out[-1] <= tol:
            continue
        out.append(v)
    return out


def _nearest(values: list[float], v: float) -> int:
    return min(range(len(values)), key=lambda i: abs(values[i] - v))


def merge_tables(tables: list[Table]) -> Table | None:
    """Gộp nhiều bảng rời trên CÙNG một trang thành một bảng duy nhất.

    Giữ nguyên vị trí thật: lấy hợp các đường kẻ dọc/ngang của mọi bảng làm
    lưới chung, nên khoảng trống giữa các bảng vẫn còn (đúng như trang PDF) và
    cả trang in ra vẫn nằm gọn trong một tờ A4.
    """
    usable = [
        t for t in tables
        if len(t.col_edges) == t.num_cols + 1
        and len(t.row_edges) == t.num_rows + 1
    ]
    if not usable:
        return None
    if len(usable) == 1:
        return usable[0]

    xs = _cluster_edges([e for t in usable for e in t.col_edges])
    ys = _cluster_edges([e for t in usable for e in t.row_edges])

    cells: dict[tuple[int, int], TableCell] = {}
    for t in usable:
        for c in t.cells:
            c1 = min(c.col + c.colspan, t.num_cols)
            r1 = min(c.row + c.rowspan, t.num_rows)
            r0 = _nearest(ys, t.row_edges[c.row])
            cc0 = _nearest(xs, t.col_edges[c.col])
            r_end = _nearest(ys, t.row_edges[r1])
            c_end = _nearest(xs, t.col_edges[c1])
            key = (r0, cc0)
            old = cells.get(key)
            if old is not None:
                # hai bảng chồng mép nhau: ưu tiên ô có chữ
                if old.text.strip() or not c.text.strip():
                    continue
            cells[key] = TableCell(
                row=r0, col=cc0,
                rowspan=max(1, r_end - r0),
                colspan=max(1, c_end - cc0),
                text=c.text, score=c.score,
            )

    # bỏ ô nằm trọn trong một ô lớn hơn (tránh merge chồng lên nhau trong Excel)
    ordered = sorted(cells.values(), key=lambda c: c.rowspan * c.colspan, reverse=True)
    kept: list[TableCell] = []
    for c in ordered:
        inside = any(
            k.row <= c.row and k.col <= c.col
            and k.row + k.rowspan >= c.row + c.rowspan
            and k.col + k.colspan >= c.col + c.colspan
            for k in kept
        )
        if not inside:
            kept.append(c)

    first = usable[0]
    num_rows, num_cols = len(ys) - 1, len(xs) - 1
    return Table(
        page=first.page,
        index=1,
        num_rows=num_rows,
        num_cols=num_cols,
        cells=kept,
        engine=first.engine,
        col_widths=[xs[i + 1] - xs[i] for i in range(num_cols)],
        row_heights=[ys[i + 1] - ys[i] for i in range(num_rows)],
        col_edges=xs,
        row_edges=ys,
        page_size=first.page_size,
    )


def _cluster_lines(items: list[TextItem]) -> list[list[TextItem]]:
    if not items:
        return []
    tol_bottom = max(3.0, 0.35 * _estimate_line_height(items))
    ordered = sorted(items, key=lambda i: (i.cy, i.x0))
    lines: list[list[TextItem]] = []
    cur = [ordered[0]]
    for it in ordered[1:]:
        if _same_line(cur, it, tol_bottom):
            cur.append(it)
        else:
            lines.append(cur)
            cur = [it]
    lines.append(cur)
    return lines


def whitespace_tables(
    items: list[TextItem], page_no: int, engine: str
) -> list[Table]:
    """Fallback cho bảng KHÔNG có đường kẻ: chia cột theo khoảng trắng."""
    lines = _cluster_lines(items)
    if len(lines) < 3:
        return []
    avg_h = _estimate_line_height(items)

    # tách mỗi dòng thành các "ô" theo khoảng trắng lớn
    line_cells: list[list[tuple[float, float, str]]] = []
    for ln in lines:
        ln = sorted(ln, key=lambda i: i.x0)
        cur: list[TextItem] = [ln[0]]
        cells: list[tuple[float, float, str]] = []
        for it in ln[1:]:
            if it.x0 - cur[-1].x1 > 1.4 * avg_h:
                cells.append(
                    (cur[0].x0, cur[-1].x1, "".join(c.text for c in cur).strip())
                )
                cur = [it]
            else:
                cur.append(it)
        cells.append((cur[0].x0, cur[-1].x1, "".join(c.text for c in cur).strip()))
        line_cells.append([c for c in cells if c[2]])

    line_cells = [c for c in line_cells if c]
    if len(line_cells) < 3:
        return []

    max_cells = max(len(c) for c in line_cells)
    if max_cells < 2:
        return []

    # neo cột theo x0
    tol = max(8.0, 0.9 * avg_h)
    anchors: list[float] = []
    for cells in line_cells:
        for x0, _, _ in cells:
            if all(abs(x0 - a) > tol for a in anchors):
                anchors.append(x0)
    anchors.sort()

    grid = [["" for _ in range(len(anchors))] for _ in line_cells]
    for ri, cells in enumerate(line_cells):
        for x0, _, txt in cells:
            ci = min(range(len(anchors)), key=lambda k: abs(anchors[k] - x0))
            grid[ri][ci] = (grid[ri][ci] + " " + txt).strip() if grid[ri][ci] else txt

    # bỏ cột rỗng
    keep = [ci for ci in range(len(anchors)) if any(row[ci] for row in grid)]
    if len(keep) < 2:
        return []
    grid = [[row[ci] for ci in keep] for row in grid]

    # bỏ dòng rỗng
    grid = [row for row in grid if any(row)]
    if len(grid) < MIN_TABLE_CELLS or len(grid) < 2:
        return []

    cells = [
        TableCell(row=ri, col=ci, text=txt)
        for ri, row in enumerate(grid)
        for ci, txt in enumerate(row)
        if txt
    ]
    return [
        Table(
            page=page_no,
            index=1,
            num_rows=len(grid),
            num_cols=len(grid[0]),
            cells=cells,
            engine=engine + "+whitespace",
        )
    ]
