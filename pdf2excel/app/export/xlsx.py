"""Xuất kết quả ra Excel (.xlsx) bằng openpyxl."""
from __future__ import annotations

import math
import re
from pathlib import Path

from openpyxl import Workbook
from openpyxl.comments import Comment
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.page import PageMargins
from openpyxl.worksheet.worksheet import Worksheet

from ..config import OCR_WARN_SCORE
from ..core.models import DocResult, Table, TableCell
from ..core.tables import merge_tables
from ..core.textlayer import is_cjk

# Ký tự không hợp lệ với XML: C0 control (trừ \t \n), C1, surrogate lẻ,
# và noncharacter (U+FFFE/U+FFFF, U+FDD0..U+FDEF). PDF hay map ký hiệu
# (ví dụ "Φ") sang U+FFFE, openpyxl sẽ báo lỗi nếu không loại bỏ.
_ILLEGAL = re.compile(
    "[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f-\u009f"
    "\ud800-\udfff\ufdd0-\ufdef\ufffe\uffff]"
)

_THIN = Side(style="thin", color="B0B0B0")
_BORDER = Border(left=_THIN, right=_THIN, top=_THIN, bottom=_THIN)
_HEAD_FILL = PatternFill("solid", fgColor="E8EEF7")
_WARN_FILL = PatternFill("solid", fgColor="FFF2CC")

# Cỡ chữ chọn theo khổ giấy: form A4 của PDF thường dùng ~9 pt cho cả tiêu đề
# lẫn nội dung, nên 9 pt giữ được đúng bố cục mà không phải thu nhỏ khi in.
_FONT_PT = 9.0
_HEAD_FONT = Font(bold=True, size=_FONT_PT)
_BODY_FONT = Font(size=_FONT_PT)
_LINE_PT = 12.0          # chiều cao một dòng ở cỡ 9 pt
_CELL_PAD_PT = 3.0
# _display_width() đếm theo "ký tự" của font mặc định 11 pt; đổi sang 9 pt.
_WIDTH_FACTOR = _FONT_PT / 11.0

_MAX_COL_WIDTH = 60
_MIN_COL_WIDTH = 8
_MIN_ROW_HEIGHT = 4.0
_MAX_ROW_HEIGHT = 409.0

# --- bố cục in: mỗi sheet = 1 trang A4 -------------------------------
_A4_W_PT, _A4_H_PT = 595.28, 841.89       # A4 ở 72 dpi (point)
_MARGIN_IN = 0.3                           # lề dự phòng khi thiếu toạ độ PDF

# Calibri 11 (font mặc định của workbook): 1 "ký tự" rộng 7 px ở 96 dpi,
# mỗi cột cộng thêm 5 px lề trong; 1 px = 0,75 pt.
_CHAR_PX = 7.0
_COL_PAD_PX = 5.0
_PX_TO_PT = 0.75


def _clean(text: str) -> str:
    text = _ILLEGAL.sub("", text or "")
    # noncharacter ở các mặt phẳng phụ (U+1FFFE, U+2FFFF, ...)
    if any((ord(ch) & 0xFFFE) == 0xFFFE for ch in text):
        text = "".join(ch for ch in text if (ord(ch) & 0xFFFE) != 0xFFFE)
    return text


def _soft_join(text: str) -> str:
    """Nối các dòng của một ô thành một đoạn, KHÔNG chèn xuống dòng cứng.

    Trước đây mỗi dòng trong ô được ghi kèm "\\n" (tương đương Alt+Enter), nên
    Excel hiển thị đúng chỗ ngắt của bản PDF nhưng không bao giờ tự dàn lại
    theo độ rộng cột. Nay chỉ nối lại thành câu và để `wrap_text` tự xuống
    dòng. Giữa hai chữ Latin cần một khoảng trắng (PDF tách dòng giữa từ), còn
    cạnh chữ Hán/CJK thì nối liền.
    """
    parts = [p.strip() for p in (text or "").split("\n")]
    parts = [p for p in parts if p]
    if not parts:
        return ""
    out = parts[0]
    for part in parts[1:]:
        if is_cjk(out[-1:]) or is_cjk(part[:1]):
            out += part
        else:
            out += " " + part
    return out


def _set_text(cell, text: str) -> None:
    text = _clean(text)
    # Ô không có chữ phải là ô TRỐNG thật (bỏ hẳn giá trị). Nếu gán chuỗi rỗng,
    # openpyxl ghi <is><t></t></is> và Excel coi là ô có giá trị (COUNTA đếm,
    # "Go To Special → Blanks" không chọn được) dù hiển thị trống.
    cell.value = text or None
    # tránh openpyxl hiểu nhầm nội dung bắt đầu bằng "=" thành công thức
    if isinstance(cell.value, str) and cell.value.startswith("="):
        cell.data_type = "s"


def _display_width(s: str) -> int:
    """Độ rộng hiển thị (ký tự CJK tính bằng 2)."""
    w = 0
    for ch in s:
        w += 2 if ord(ch) > 0x2E7F else 1
    return w


def _sheet_title(table: Table, used: set[str], base: str | None = None) -> str:
    base = base or f"T{table.page}-B{table.index}"
    title = base
    n = 2
    while title in used:
        title = f"{base}_{n}"
        n += 1
    used.add(title)
    return title[:31]


def _apply_overrides(table: Table, overrides: dict[str, str]) -> Table:
    """Bản sao của bảng với các ô đã được người dùng sửa trên web."""
    if not overrides:
        return table
    cells: list[TableCell] = []
    for c in table.cells:
        text = overrides.get(f"{c.row},{c.col}", c.text)
        cells.append(
            TableCell(
                row=c.row, col=c.col,
                rowspan=c.rowspan, colspan=c.colspan,
                text=text, score=c.score,
            )
        )
    return Table(
        page=table.page, index=table.index,
        num_rows=table.num_rows, num_cols=table.num_cols,
        cells=cells, engine=table.engine, bbox=table.bbox,
        col_widths=table.col_widths, row_heights=table.row_heights,
        col_edges=table.col_edges, row_edges=table.row_edges,
        page_size=table.page_size,
    )


def _write_table(ws: Worksheet, table: Table, overrides: dict[str, str]) -> None:
    grid = table.grid()
    spans = {(c.row, c.col): (c.rowspan, c.colspan) for c in table.cells}
    scores = {(c.row, c.col): c.score for c in table.cells}

    for key, val in (overrides or {}).items():
        try:
            r_s, c_s = key.split(",")
            r, c = int(r_s), int(c_s)
        except Exception:
            continue
        if 0 <= r < table.num_rows and 0 <= c < table.num_cols:
            grid[r][c] = val

    for (r, c), (rs, cs) in spans.items():
        cell = ws.cell(row=r + 1, column=c + 1)
        raw = grid[r][c] if r < len(grid) and c < len(grid[r]) else ""
        _set_text(cell, _soft_join(str(raw)))
        cell.font = _BODY_FONT
        cell.alignment = Alignment(
            wrap_text=True, vertical="center", horizontal="left"
        )
        score = scores.get((r, c))
        if score is not None and score < OCR_WARN_SCORE:
            cell.fill = _WARN_FILL
            cell.comment = Comment(f"Độ tin cậy OCR thấp: {score:.2f}", "pdf2excel")

    # Chỉ tô tiêu đề / kẻ viền cho ô CÓ THẬT. Với trang gộp nhiều bảng, phần
    # trống giữa các bảng sẽ không bị kẻ thành lưới rối.
    if table.num_rows >= 1:
        for (r, c) in spans:
            if r != 0:
                continue
            cell = ws.cell(row=1, column=c + 1)
            cell.font = _HEAD_FONT
            cell.fill = _HEAD_FILL

    for (r, c), (rs, cs) in spans.items():
        for rr in range(r, min(table.num_rows, r + rs)):
            for cc in range(c, min(table.num_cols, c + cs)):
                ws.cell(row=rr + 1, column=cc + 1).border = _BORDER

    for (r, c), (rs, cs) in spans.items():
        if rs > 1 or cs > 1:
            ws.merge_cells(
                start_row=r + 1, start_column=c + 1,
                end_row=r + rs, end_column=c + cs,
            )

    # --- độ rộng cột -------------------------------------------------
    # Ưu tiên kích thước THẬT của bản PDF (lấy từ đường kẻ): nhờ vậy bảng in ra
    # có tỉ lệ giống bản gốc, không bị cột rộng/nhỏ tuỳ theo độ dài chữ.
    geom_cols = (
        table.col_widths if len(table.col_widths) == table.num_cols else None
    )
    if geom_cols:
        col_width = [_pt_to_chars(pt) for pt in geom_cols]
    else:
        col_width = [_MIN_COL_WIDTH] * table.num_cols
        for (r, cc), (rs, cs) in spans.items():
            val = grid[r][cc] if r < len(grid) and cc < len(grid[r]) else ""
            longest = max(
                (_display_width(x) for x in str(val).split("\n")), default=0
            )
            share = int(longest / max(1, cs)) + 3
            for k in range(cc, min(table.num_cols, cc + cs)):
                col_width[k] = max(col_width[k], min(_MAX_COL_WIDTH, share))
    for c in range(table.num_cols):
        ws.column_dimensions[get_column_letter(c + 1)].width = col_width[c]

    # --- chiều cao dòng -----------------------------------------------
    # Lấy chiều cao thật, nhưng vẫn nới thêm nếu chữ tự xuống dòng cần nhiều
    # dòng hơn (tránh bị Excel cắt chữ khi in). Ô gộp nhiều hàng chia đều.
    geom_rows = (
        table.row_heights if len(table.row_heights) == table.num_rows else None
    )
    for r in range(table.num_rows):
        need = 1.0
        has_text = False
        for (rr, cc), (rs, cs) in spans.items():
            if rr != r:
                continue
            val = grid[r][cc] if r < len(grid) and cc < len(grid[r]) else ""
            text = _soft_join(str(val))
            if text.strip():
                has_text = True
            avail = max(2.0, sum(col_width[cc:cc + cs]) - 0.4)
            lines = max(1, math.ceil(_display_width(text) * _WIDTH_FACTOR / avail))
            if scores.get((rr, cc)) is not None and (scores[(rr, cc)] or 1) < OCR_WARN_SCORE:
                lines += 1
            need = max(need, lines / max(1, rs))
        height = math.ceil(need) * _LINE_PT + _CELL_PAD_PT if has_text else 0.0
        if geom_rows:
            height = max(height, geom_rows[r])
        ws.row_dimensions[r + 1].height = max(
            _MIN_ROW_HEIGHT, min(_MAX_ROW_HEIGHT, height)
        )

    ws.freeze_panes = "A2"
    _setup_print(ws, table, col_width)


def _pt_to_chars(pt: float) -> float:
    """Bề rộng vật lý (pt) -> độ rộng cột Excel (đơn vị ký tự)."""
    chars = (pt / _PX_TO_PT - _COL_PAD_PX) / _CHAR_PX
    return max(1.0, min(200.0, chars))


def _chars_to_pt(chars: float) -> float:
    return (chars * _CHAR_PX + _COL_PAD_PX) * _PX_TO_PT


def _setup_print(ws: Worksheet, table: Table, col_width: list[float]) -> None:
    """Đặt trang in = đúng 1 trang A4, cùng chiều với trang PDF.

    Lề trên/dưới/trái/phải lấy theo đúng vị trí bảng trên trang PDF, nên bảng
    in ra nằm đúng chỗ như bản gốc. Sim "Fit to 1 page" luôn bật để chắc chắn
    mỗi sheet chỉ chiếm một trang (Excel chỉ thu nhỏ, không phóng to, nên bảng
    nhỏ vẫn in ở 100%).
    """
    ws.page_setup.paperSize = ws.PAPERSIZE_A4

    total_w = sum(_chars_to_pt(w) for w in col_width)
    total_h = sum(
        (ws.row_dimensions[r + 1].height or _MIN_ROW_HEIGHT)
        for r in range(table.num_rows)
    )

    page_w, page_h = table.page_size or (0.0, 0.0)
    landscape = (page_w > page_h) if page_w and page_h else (total_w > total_h)
    ws.page_setup.orientation = "landscape" if landscape else "portrait"

    paper_w = _A4_H_PT if landscape else _A4_W_PT
    paper_h = _A4_W_PT if landscape else _A4_H_PT

    left = top = right = bottom = _MARGIN_IN
    if (
        page_w
        and page_h
        and len(table.col_edges) == table.num_cols + 1
        and len(table.row_edges) == table.num_rows + 1
    ):
        # bảng bắt đầu/kết thúc ở đâu trên trang PDF -> lề tương ứng (inch)
        k = min(paper_w / page_w, paper_h / page_h)
        left = max(0.0, table.col_edges[0]) * k / 72
        top = max(0.0, table.row_edges[0]) * k / 72
        right = max(0.0, page_w - table.col_edges[-1]) * k / 72
        bottom = max(0.0, page_h - table.row_edges[-1]) * k / 72
        # chừa tối thiểu 0,12" cho lề và luôn còn ít nhất 0,6" cho vùng in
        left, right = max(0.12, left), max(0.12, right)
        top, bottom = max(0.12, top), max(0.12, bottom)
        span_w = paper_w / 72 - 0.6
        if left + right > span_w:
            s = span_w / (left + right)
            left, right = left * s, right * s
        span_h = paper_h / 72 - 0.6
        if top + bottom > span_h:
            s = span_h / (top + bottom)
            top, bottom = top * s, bottom * s

    ws.page_margins = PageMargins(
        left=round(left, 3),
        right=round(right, 3),
        top=round(top, 3),
        bottom=round(bottom, 3),
        header=0.0,
        footer=0.0,
    )
    # lề đã đặt đúng vị trí gốc nên không canh giữa nữa (canh giữa sẽ lệch)
    ws.print_options.horizontalCentered = False
    ws.sheet_view.showGridLines = False
    ws.print_area = f"A1:{get_column_letter(table.num_cols)}{table.num_rows}"

    if not total_w or not total_h:
        return
    # luôn bật: mỗi sheet = đúng 1 trang A4, không bao giờ tràn sang trang 2
    ws.page_setup.fitToPage = True
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 1


def build_workbook(
    result: DocResult,
    include: set[str] | None = None,
    edits: dict[str, dict[str, str]] | None = None,
) -> Workbook:
    """Chỉ ghi các sheet bảng. Không tạo sheet phụ (_Thông tin / _Văn bản gốc).

    Mỗi trang PDF là một sheet: các bảng rời trên cùng một trang được gộp lại
    (giữ nguyên vị trí) để mở lên/in ra vẫn đúng như trang gốc.
    """
    wb = Workbook()
    default = wb.active
    default.title = "Kết quả"
    edits = edits or {}

    groups: list[list[Table]] = []
    for table in result.tables:
        if include is not None and table.id not in include:
            continue
        if groups and groups[-1][0].page == table.page:
            groups[-1].append(table)
        else:
            groups.append([table])

    used: set[str] = set()
    first = True

    def _emit(table: Table, title: str | None) -> None:
        nonlocal first
        if first:
            ws = default
            ws.title = _sheet_title(table, used, title)
            first = False
        else:
            ws = wb.create_sheet(_sheet_title(table, used, title))
        _write_table(ws, table, {})

    for group in groups:
        prepared = [_apply_overrides(t, edits.get(t.id, {})) for t in group]
        if len(prepared) > 1:
            merged = merge_tables(prepared)
            if merged is not None:
                _emit(merged, f"T{group[0].page}")
                continue
        for t in prepared:
            _emit(t, None)

    if first:
        # không có bảng nào: giữ một sheet trống để file Excel vẫn hợp lệ
        ws = default
        ws.title = "Kết quả"
        ws["A1"] = "Không có bảng nào được chọn để xuất."
        ws.column_dimensions["A"].width = 50

    return wb


def save_workbook(
    result: DocResult,
    path: Path,
    include: set[str] | None = None,
    edits: dict[str, dict[str, str]] | None = None,
) -> Path:
    wb = build_workbook(result, include=include, edits=edits)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    return path
