"""Tạo PDF vector tối giản (có bảng kẻ + chữ) để test, không cần thư viện ngoài."""
from __future__ import annotations

from pathlib import Path


def _esc(text: str) -> str:
    return text.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")


def build_table_pdf(
    path: str | Path,
    header: tuple[str, ...] = ("Code", "Name", "Qty"),
    rows: tuple[tuple[str, ...], ...] = (("A1", "Bolt M6", "10"), ("A2", "Nut M6", "20")),
    page: tuple[float, float] = (595, 842),
) -> Path:
    """Một trang A4 với 1 bảng kẻ 3 cột."""
    col_w = [80, 150, 70]
    row_h = 24.0
    x0 = 60.0
    y_top = page[1] - 80.0
    n_rows = 1 + len(rows)
    total_w = float(sum(col_w))

    ops: list[str] = ["0.9 w 0 0 0 RG"]

    # đường ngang
    for i in range(n_rows + 1):
        y = y_top - i * row_h
        ops.append(f"{x0} {y:.1f} m {x0 + total_w:.1f} {y:.1f} l S")

    # đường dọc
    x = x0
    ops.append(f"{x0:.1f} {y_top:.1f} m {x0:.1f} {y_top - n_rows * row_h:.1f} l S")
    for w in col_w:
        x += w
        ops.append(f"{x:.1f} {y_top:.1f} m {x:.1f} {y_top - n_rows * row_h:.1f} l S")

    # chữ
    y_text = y_top - row_h + 7
    x_cell = x0
    for i, w in enumerate(col_w):
        ops.append(
            f"BT /F1 11 Tf 1 0 0 1 {x_cell + 5:.1f} {y_text:.1f} Tm ({_esc(header[i])}) Tj ET"
        )
        x_cell += w
    for r_idx, row in enumerate(rows, start=1):
        y_text = y_top - (r_idx + 1) * row_h + 7
        x_cell = x0
        for i, w in enumerate(col_w):
            ops.append(
                f"BT /F1 11 Tf 1 0 0 1 {x_cell + 5:.1f} {y_text:.1f} Tm ({_esc(row[i])}) Tj ET"
            )
            x_cell += w

    content = "\n".join(ops).encode("latin-1")
    objs: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {page[0]} {page[1]}] "
            f"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>"
        ).encode(),
        b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
    ]

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
