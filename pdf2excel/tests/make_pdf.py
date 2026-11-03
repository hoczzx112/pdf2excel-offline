"""Tạo PDF vector tối giản (có bảng kẻ + chữ) để test, không cần thư viện ngoài."""
from __future__ import annotations

from pathlib import Path


def _esc(text: str) -> str:
    return text.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")


def _to_pdf(x: float, y: float, rotate: int, page_h: float) -> tuple[float, float]:
    """Toạ độ "nhìn thấy" (y hướng xuống) -> toạ độ PDF chưa xoay (y hướng lên).

    Trang /Rotate 90 hiển thị nội dung đã xoay, nên muốn bảng nằm ngang trên
    màn hình thì phải vẽ nó xoay ngược lại trong toạ độ gốc.
    """
    if rotate == 90:
        return y, x
    if rotate:
        raise ValueError("make_pdf chỉ hỗ trợ rotate=0 hoặc 90")
    return x, page_h - y


def _line(x0: float, y0: float, x1: float, y1: float, rotate: int, page_h: float) -> str:
    u0, v0 = _to_pdf(x0, y0, rotate, page_h)
    u1, v1 = _to_pdf(x1, y1, rotate, page_h)
    return f"{u0:.1f} {v0:.1f} m {u1:.1f} {v1:.1f} l S"


def _text(x: float, y: float, text: str, rotate: int, page_h: float) -> str:
    u, v = _to_pdf(x, y, rotate, page_h)
    if rotate == 90:
        return f"BT /F1 11 Tf 0 1 -1 0 {u:.1f} {v:.1f} Tm ({_esc(text)}) Tj ET"
    return f"BT /F1 11 Tf 1 0 0 1 {u:.1f} {v:.1f} Tm ({_esc(text)}) Tj ET"


def _circle_slash(cx: float, cy: float, rx: float, ry: float,
                  rotate: int, page_h: float) -> str:
    """Ký hiệu ø vẽ bằng NÉT (elip + gạch chéo), không phải chữ.

    Giống cách AutoCAD vẽ ký hiệu đường kính: text layer không có ký tự nào,
    nên chỉ nhận ra được nếu đọc hình học.
    """
    k = 0.5523
    pts = [
        (cx + rx, cy),
        (cx + rx, cy - k * ry), (cx + k * rx, cy - ry), (cx, cy - ry),
        (cx - k * rx, cy - ry), (cx - rx, cy - k * ry), (cx - rx, cy),
        (cx - rx, cy + k * ry), (cx - k * rx, cy + ry), (cx, cy + ry),
        (cx + k * rx, cy + ry), (cx + rx, cy + k * ry), (cx + rx, cy),
    ]
    mapped = [_to_pdf(px, py, rotate, page_h) for px, py in pts]
    ops = [f"{mapped[0][0]:.1f} {mapped[0][1]:.1f} m"]
    for i in range(1, len(mapped), 3):
        (a, b), (c, d), (e, f) = mapped[i], mapped[i + 1], mapped[i + 2]
        ops.append(
            f"{a:.1f} {b:.1f} {c:.1f} {d:.1f} {e:.1f} {f:.1f} c"
        )
    ops.append("h")
    # gạch chéo cùng một path để pdfium trả về một hộp bao duy nhất
    s0 = _to_pdf(cx - 0.8 * rx, cy + 0.9 * ry, rotate, page_h)
    s1 = _to_pdf(cx + 0.8 * rx, cy - 0.9 * ry, rotate, page_h)
    ops.append(f"{s0[0]:.1f} {s0[1]:.1f} m {s1[0]:.1f} {s1[1]:.1f} l S")
    return " ".join(ops)


def build_table_pdf(
    path: str | Path,
    header: tuple[str, ...] = ("Code", "Name", "Qty"),
    rows: tuple[tuple[str, ...], ...] = (("A1", "Bolt M6", "10"), ("A2", "Nut M6", "20")),
    page: tuple[float, float] = (595, 842),
    rotate: int = 0,
    page_count: int = 1,
    diameter: bool = False,
    second_y: float | None = None,
) -> Path:
    """Một hoặc nhiều trang A4, mỗi trang có 1 bảng kẻ 3 cột.

    rotate: ghi /Rotate vào trang (90 = trang nằm ngang nhưng lưu dạng dọc).
    page_count: số trang, nội dung giống nhau.
    diameter: vẽ thêm ký hiệu ø bằng nét vector trước giá trị Qty hàng 1.
    second_y: nếu có, vẽ thêm một bảng nhỏ 2 cột ở vị trí này (cùng trang) để
        kiểm tra việc gộp nhiều bảng rời vào chung một sheet.
    """
    disp_w, disp_h = (page[1], page[0]) if rotate in (90, 270) else (page[0], page[1])

    col_w = [80, 150, 70]
    row_h = 24.0
    x0 = 60.0
    y_top = 80.0                    # cách mép trên 80pt, toạ độ hiển thị (y hướng xuống)
    n_rows = 1 + len(rows)
    total_w = float(sum(col_w))

    ops: list[str] = ["0.9 w 0 0 0 RG"]

    # đường ngang
    for i in range(n_rows + 1):
        y = y_top + i * row_h
        ops.append(_line(x0, y, x0 + total_w, y, rotate, page[1]))

    # đường dọc
    x = x0
    ops.append(_line(x0, y_top, x0, y_top + n_rows * row_h, rotate, page[1]))
    for w in col_w:
        x += w
        ops.append(_line(x, y_top, x, y_top + n_rows * row_h, rotate, page[1]))

    # chữ (baseline cách đáy ô 7pt như bản gốc)
    x_cell = x0
    for i, w in enumerate(col_w):
        ops.append(_text(x_cell + 5, y_top + row_h - 7, header[i], rotate, page[1]))
        x_cell += w
    for r_idx, row in enumerate(rows, start=1):
        y_text = y_top + (r_idx + 1) * row_h - 7
        x_cell = x0
        for i, w in enumerate(col_w):
            if diameter and r_idx == 1 and i == 2:
                # ký hiệu ø nằm ngay trước giá trị, chữ dịch sang phải 9pt
                ops.append(
                    _circle_slash(
                        x_cell + 5 + 2.75, y_text - 4.5, 2.75, 4.5, rotate, page[1]
                    )
                )
                ops.append(_text(x_cell + 5 + 9, y_text, row[i], rotate, page[1]))
            else:
                ops.append(_text(x_cell + 5, y_text, row[i], rotate, page[1]))
            x_cell += w

    # bảng thứ hai (rời hẳn bảng thứ nhất) để test gộp bảng cùng trang
    if second_y is not None:
        sw = [90.0, 90.0]
        sy = float(second_y)
        ops.append(_line(x0, sy, x0 + sum(sw), sy, rotate, page[1]))
        ops.append(_line(x0, sy + 2 * row_h, x0 + sum(sw), sy + 2 * row_h,
                         rotate, page[1]))
        ops.append(_line(x0, sy + row_h, x0 + sum(sw), sy + row_h,
                         rotate, page[1]))
        x = x0
        ops.append(_line(x0, sy, x0, sy + 2 * row_h, rotate, page[1]))
        for w in sw:
            x += w
            ops.append(_line(x, sy, x, sy + 2 * row_h, rotate, page[1]))
        ops.append(_text(x0 + 5, sy + row_h - 7, "Sub", rotate, page[1]))
        ops.append(_text(x0 + sw[0] + 5, sy + row_h - 7, "Total", rotate, page[1]))
        ops.append(_text(x0 + 5, sy + 2 * row_h - 7, "S1", rotate, page[1]))
        ops.append(_text(x0 + sw[0] + 5, sy + 2 * row_h - 7, "7", rotate, page[1]))

    content = "\n".join(ops).encode("latin-1")
    n = max(1, page_count)
    font_id = 3 + 2 * n                    # 1 catalog, 2 pages, rồi (page, content) * n
    rot = f" /Rotate {int(rotate)}" if rotate else ""

    objs: list[bytes] = [b"", b""]          # chỗ giữ: 1 catalog, 2 pages
    kids: list[str] = []
    for k in range(n):
        pid, cid = 3 + 2 * k, 4 + 2 * k
        kids.append(f"{pid} 0 R")
        objs.append(
            (
                f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {page[0]} {page[1]}]"
                f"{rot} /Resources << /Font << /F1 {font_id} 0 R >> >> /Contents {cid} 0 R >>"
            ).encode()
        )
        objs.append(
            b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n"
            + content + b"\nendstream"
        )
    objs[0] = b"<< /Type /Catalog /Pages 2 0 R >>"
    objs[1] = (
        f"<< /Type /Pages /Kids [{' '.join(kids)}] /Count {n} >>"
    ).encode()
    objs.append(
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>"
    )

    out = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for i, body in enumerate(objs, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + body + b"\nendobj\n"

    xref_pos = len(out)
    out += f"xref\n0 {len(objs) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for off in offsets[1:]:
        out += f"{off:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref_pos}\n%%EOF\n"
    ).encode()

    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(bytes(out))
    return p
