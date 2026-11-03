"""Nhận ký hiệu được vẽ bằng nét vector (không có trong text layer).

Bản vẽ AutoCAD / file xuất từ CAD thường vẽ **ký hiệu đường kính** (ø) bằng
hình học — một elip bị gạch chéo — chứ không dùng chữ. Khi đó text layer
không có ký tự nào cho ký hiệu này, nên ô Excel ra `73(0/-0.009)` thay vì
`ø73(0/-0.009)`. Đây là lỗi dữ liệu của file PDF, không phải lỗi đọc chữ.

Cách nhận: mỗi ký hiệu là một đối tượng *path* riêng, kích thước cỡ một ký tự;
ảnh render của nó là một vòng kín bị gạch chéo chia làm **đúng 2 lỗ** thông
nhau, hai lỗ đều nhỏ và gần bằng nhau. Các nét kẻ bảng (dài, mảnh) và ô
checkbox (1 lỗ to, hoặc 2 lỗ to không cân) bị loại.
"""
from __future__ import annotations

import cv2
import numpy as np

from .models import TextItem

PATH_OBJECT = 2            # FPDF_PAGEOBJ_PATH
MAX_GLYPH_PT = 30.0        # ký hiệu lớn hơn mức này là hình vẽ, không phải chữ
DIAMETER = "\u00f8"


def _page_frame(page) -> tuple[int, float, float, float, float]:
    """Giống textlayer._page_geometry (giữ cục bộ để tránh import vòng)."""
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
    if w0 <= 0 or h0 <= 0:
        w, h = page.get_size()
        left = bottom = 0.0
        w0, h0 = (h, w) if rot in (90, 270) else (w, h)
    return rot, left, bottom, w0, h0


def _to_pixels(rot, left, bottom, right, top, off_x, off_y, w0, h0):
    """Hộp bao (hệ trang chưa xoay, y hướng lên) -> pixel ảnh đã xoay."""
    left -= off_x
    right -= off_x
    bottom -= off_y
    top -= off_y
    if rot == 90:
        return bottom, left, top, right
    if rot == 180:
        return w0 - right, h0 - top, w0 - left, h0 - bottom
    if rot == 270:
        return h0 - top, w0 - right, h0 - bottom, w0 - left
    return left, h0 - top, right, h0 - bottom


def looks_like_diameter(crop: np.ndarray) -> bool:
    """True nếu vùng ảnh là vòng kín bị gạch chéo (ký hiệu ø)."""
    if crop.size == 0:
        return False
    ink = (crop < 128).astype(np.uint8)
    if int(ink.sum()) < 12:
        return False
    h, w = ink.shape
    n, _labels, stats, _cent = cv2.connectedComponentsWithStats(
        1 - ink, connectivity=4
    )
    holes: list[tuple[int, int, int]] = []
    for i in range(1, n):
        x, y, cw, ch, area = (int(v) for v in stats[i][:5])
        if x <= 0 or y <= 0 or x + cw >= w or y + ch >= h:
            continue                      # chạm biên = nền ngoài vòng
        holes.append((area, cw, ch))
    if len(holes) != 2:                   # ø = bị gạch chia làm đúng 2 lỗ
        return False
    frac = sum(a for a, _, _ in holes) / float(w * h)
    if not 0.12 <= frac <= 0.38:
        return False
    a1, a2 = holes[0][0], holes[1][0]
    if max(a1, a2) == 0 or min(a1, a2) / float(max(a1, a2)) < 0.55:
        return False
    for _area, cw, ch in holes:
        if cw > 0.60 * w or ch > 0.80 * h:
            return False
    return True


def vector_glyph_items(
    page, img: np.ndarray, scale: float, existing: list[TextItem] | None = None
) -> list[TextItem]:
    """Các ký hiệu vector đoán được (hiện chỉ có ø) -> danh sách TextItem."""
    rot, off_x, off_y, w0, h0 = _page_frame(page)
    gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY) if img.ndim == 3 else img
    height, width = gray.shape[:2]

    out: list[TextItem] = []
    try:
        objects = list(page.get_objects())
    except Exception:
        return out

    for obj in objects:
        if getattr(obj, "type", None) != PATH_OBJECT:
            continue
        try:
            left, bottom, right, top = obj.get_bounds()
        except Exception:
            continue
        pw, ph = right - left, top - bottom
        if not (2.0 <= pw <= MAX_GLYPH_PT and 2.0 <= ph <= MAX_GLYPH_PT):
            continue
        if not (0.35 <= pw / ph <= 2.8):
            continue

        x0, y0, x1, y1 = _to_pixels(rot, left, bottom, right, top, off_x, off_y, w0, h0)
        ix0 = max(0, int(x0 * scale))
        iy0 = max(0, int(y0 * scale))
        ix1 = min(width, int(x1 * scale) + 1)
        iy1 = min(height, int(y1 * scale) + 1)
        if ix1 - ix0 < 6 or iy1 - iy0 < 6:
            continue

        if not looks_like_diameter(gray[iy0:iy1, ix0:ix1]):
            continue

        box = (ix0, iy0, ix1, iy1)
        if existing and any(_covers_existing(box, it) for it in existing):
            continue                      # ký hiệu đã có trong text layer
        out.append(
            TextItem(
                x0=ix0, y0=iy0, x1=ix1, y1=iy1, text=DIAMETER, synthetic=True
            )
        )
    return out


def _covers_existing(box, item: TextItem, ratio: float = 0.5) -> bool:
    """Ký hiệu vector trùng chỗ với chữ đã có -> bỏ (tránh nhân đôi)."""
    ix = min(box[2], item.x1) - max(box[0], item.x0)
    iy = min(box[3], item.y1) - max(box[1], item.y0)
    if ix <= 0 or iy <= 0:
        return False
    area = max(1, (box[2] - box[0]) * (box[3] - box[1]))
    return (ix * iy) / area > ratio
