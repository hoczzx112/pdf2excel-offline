"""Dựng bảng (grid) từ các ô đã dò được + chữ trong ô."""
from __future__ import annotations

import statistics

from ..config import MIN_TABLE_CELLS
from .gridlines import CellRect, group_tables
from .models import Table, TableCell, TextItem
from .textlayer import _same_line, assemble_text


def _crop_edges(
    cells: list[TableCell], num_rows: int, num_cols: int
) -> tuple[list[TableCell], int, int] | None:
    """Cắt các hàng/cột rỗng ở rìa bảng (thường do viền trang bị nhận nhầm)."""
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
        return cells, num_rows, num_cols

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
    return out, r1 - r0 + 1, c1 - c0 + 1


def _estimate_line_height(items: list[TextItem], default: float = 12.0) -> float:
    hs = [i.h for i in items if i.h > 0.5]
    return statistics.median(hs) if hs else default


def build_page_tables(
    rects: list[CellRect],
    items: list[TextItem],
    page_no: int,
    engine: str,
    page_h: float,
) -> list[Table]:
    """Gom ô -> bảng, ghép chữ vào từng ô, chuẩn hoá chỉ số hàng/cột."""
    tables: list[Table] = []
    for idx, group in enumerate(group_tables(rects, page_h), start=1):
        row_lines = sorted({r.r0 for r in group} | {r.r1 + 1 for r in group})
        col_lines = sorted({r.c0 for r in group} | {r.c1 + 1 for r in group})
        row_map = {v: i for i, v in enumerate(row_lines)}
        col_map = {v: i for i, v in enumerate(col_lines)}
        num_rows = len(row_lines) - 1
        num_cols = len(col_lines) - 1
        if num_rows < 1 or num_cols < 1 or num_rows * num_cols > 20000:
            continue

        cells: list[TableCell] = []
        for r in group:
            text, score = assemble_text(items, r.x0, r.y0, r.x1, r.y1)
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
        cells, num_rows, num_cols = cropped

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
            )
        )
    return tables


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
