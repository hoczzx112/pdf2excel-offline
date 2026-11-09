"""Nhận ký hiệu được vẽ bằng nét vector (không có trong text layer).

Bản vẽ AutoCAD / file xuất từ CAD thường vẽ **ký hiệu đường kính** (ø) bằng
hình học — một elip bị gạch chéo — chứ không dùng chữ. Khi đó text layer
không có ký tự nào cho ký hiệu này, nên ô Excel ra `73(0/-0.009)` thay vì
`ø73(0/-0.009)`. Đây là lỗi dữ liệu của file PDF, không phải lỗi đọc chữ.

Cách nhận: mỗi ký hiệu là một đối tượng *path* riêng, kích thước cỡ một ký tự;
ảnh render của nó là một vòng kín bị gạch chéo chia làm **đúng 2 lỗ** thông
nhau, hai lỗ đều nhỏ và gần bằng nhau. Các nét kẻ bảng (dài, mảnh) bị loại.

Nhiều file còn vẽ **ô vuông tick** (☐/☑) bằng nét vector: khung vuông rỗng,
bên trong có thể có dấu √ (dấu √ thì thường nằm trong text layer). Text layer
không có ký tự nào cho cái khung, nên nếu chỉ đọc chữ thì Excel mất hẳn các ô
chọn — ô ra `样件 试生产 量产` thay vì `☑样件 ☐试生产 ☐量产`.
"""
from __future__ import annotations

import cv2
import numpy as np

from .models import TextItem

PATH_OBJECT = 2            # FPDF_PAGEOBJ_PATH
MAX_GLYPH_PT = 30.0        # ký hiệu lớn hơn mức này là hình vẽ, không phải chữ
DIAMETER = "\u00f8"
CHECKBOX = "\u2610"        # ☐ ô chọn chưa tick
CHECKBOX_CHECKED = "\u2611"  # ☑ ô chọn đã tick
BOX_MIN_PT = 5.0           # ô tick thường cỡ 8–12 pt
BOX_MAX_PT = 16.0
CHECK_MARKS = frozenset("\u221a\u2713\u2714")  # √ ✓ ✔


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
    """True nếu vùng ảnh là vòng kín bị gạch chéo (ký hiệu ø).

    Nhiều file xuất từ CAD vẽ **cả chuỗi chữ** bằng nét (text ảo) — khi đó hộp
    bao của path trùng với cả cụm chữ, và hai chữ "0" cạnh nhau cũng tạo ra
    đúng 2 lỗ tròn như ký hiệu ø. Vì vậy ngoài số lỗ còn phải kiểm tra:

    * nét vẽ là **một** khối liền (vòng + gạch chéo dính nhau), không phải
      nhiều nét rời như các chữ cái;
    * hai lỗ do nét gạch chia ra, nên hoặc chồng lên nhau, hoặc nằm sát nhau
      và **giữa chúng là một nét liền** (không có khe trắng). Hai chữ `0` cạnh
      nhau cũng tạo 2 lỗ nhưng giữa là khoảng trắng; chữ `8` thì 2 lỗ xếp dọc
      và ký tự luôn cao hơn rộng.
    """
    if crop.size == 0:
        return False
    ink = (crop < 128).astype(np.uint8)
    if int(ink.sum()) < 12:
        return False
    h, w = ink.shape

    # nét vẽ phải là một khối liền (bỏ qua đốm nhiễu do khử răng cưa)
    n_ink, _lbl, st_ink, _ = cv2.connectedComponentsWithStats(ink, connectivity=8)
    if n_ink < 2:
        return False
    if int(st_ink[1:, 4].max()) < 0.9 * int(ink.sum()):
        return False

    n, _labels, stats, _cent = cv2.connectedComponentsWithStats(
        1 - ink, connectivity=4
    )
    # mỗi lỗ: (area, x0, y0, x1, y1)
    holes: list[tuple[int, float, float, float, float]] = []
    for i in range(1, n):
        x, y, cw, ch, area = (int(v) for v in stats[i][:5])
        if x <= 0 or y <= 0 or x + cw >= w or y + ch >= h:
            continue                      # chạm biên = nền ngoài vòng
        holes.append((area, float(x), float(y), float(x + cw), float(y + ch)))
    if len(holes) != 2:                   # ø = bị gạch chia làm đúng 2 lỗ
        return False
    frac = sum(h[0] for h in holes) / float(w * h)
    if not 0.12 <= frac <= 0.38:
        return False
    a1, a2 = holes[0][0], holes[1][0]
    if max(a1, a2) == 0 or min(a1, a2) / float(max(a1, a2)) < 0.55:
        return False
    for _area, x0, y0, x1, y1 in holes:
        if x1 - x0 > 0.60 * w or y1 - y0 > 0.80 * h:
            return False

    # hai lỗ phải "dính" nhau: gạch chéo của ø là một nét mảnh cắt vòng nên
    # hai nửa vòng hoặc chồng nhau, hoặc chỉ cách nhau đúng bề dày nét gạch.
    def _gap(a0: float, a1: float, b0: float, b1: float) -> float:
        return max(0.0, max(a0, b0) - min(a1, b1))

    (a1, xa0, ya0, xa1, ya1), (a2, xb0, yb0, xb1, yb1) = holes
    hw = max(xa1 - xa0, xb1 - xb0)
    hh = max(ya1 - ya0, yb1 - yb0)
    gx = _gap(xa0, xa1, xb0, xb1)
    gy = _gap(ya0, ya1, yb0, yb1)
    tol_x = max(1.0, 0.15 * hw)
    tol_y = max(1.0, 0.15 * hh)

    if gx <= tol_x and gy <= tol_y:
        return True                       # hai lỗ chồng nhau (gạch chéo 45°)

    def _bridge(i0: int, i1: int, j0: int, j1: int, axis: int) -> bool:
        """Nét nối giữa 2 lỗ có phải là mực liền (không có khe trắng)?"""
        if i1 - i0 < 1 or j1 - j0 < 1:
            return False
        strip = ink[i0:i1, j0:j1] if axis == 0 else ink[j0:j1, i0:i1]
        return strip.size > 0 and float(strip.mean()) >= 0.85

    if gy <= tol_y and gx > tol_x:
        # Gạch chéo gần như thẳng đứng: hai lỗ nằm cạnh nhau theo trục ngang.
        # Hai chữ "0" đứng cạnh nhau cũng vậy, nhưng giữa chúng là khoảng
        # trắng nên bị loại; nét gạch của ø thì liền một mạch.
        return _bridge(
            int(max(ya0, yb0)), int(min(ya1, yb1)),
            int(min(xa1, xb1)), int(max(xa0, xb0)), axis=0,
        )
    if gx <= tol_x and gy > tol_y:
        # Ký hiệu bị xoay 90° (chữ trong bản vẽ đặt dọc): hai lỗ xếp trên-
        # dưới. Chữ "8" cũng xếp dọc nhưng luôn cao hơn rộng nên bị loại.
        if w <= h:
            return False
        return _bridge(
            int(max(xa0, xb0)), int(min(xa1, xb1)),
            int(min(ya1, yb1)), int(max(ya0, yb0)), axis=1,
        )
    return False


def looks_like_checkbox(crop: np.ndarray) -> bool:
    """True nếu vùng ảnh là một khung vuông rỗng vẽ bằng nét (ô tick ☐/☑).

    Điều kiện hình học:

    * kích thước gần vuông và nét bao phủ gần hết hộp bao của path;
    * **bốn cạnh ngoài của hộp bao là nét liền** — khung vuông kín. Vòng tròn
      (ký hiệu ⊕ trong bản vẽ) hay nét chéo chỉ chạm cạnh ở vài điểm giữa nên
      bị loại;
    * bốn dải viền có độ dày gần bằng nhau (chữ cái vẽ bằng nét luôn lệch);
    * ruột rỗng (chỉ có thể có dấu tick bên trong, không phải ô đặc hay chữ
      như `回`).
    """
    if crop.size == 0:
        return False
    ink = (crop < 128).astype(np.uint8)
    h, w = ink.shape
    if h < 8 or w < 8:
        return False
    if not (0.85 <= w / h <= 1.18):
        return False
    frac = float(ink.mean())
    if not (0.18 <= frac <= 0.45):
        return False
    ys, xs = np.nonzero(ink)
    if len(xs) == 0:
        return False
    bx0, bx1 = int(xs.min()), int(xs.max())
    by0, by1 = int(ys.min()), int(ys.max())
    if bx1 - bx0 + 1 < 0.80 * w or by1 - by0 + 1 < 0.80 * h:
        return False
    edges = (
        ink[by0, bx0:bx1 + 1].mean(),
        ink[by1, bx0:bx1 + 1].mean(),
        ink[by0:by1 + 1, bx0].mean(),
        ink[by0:by1 + 1, bx1].mean(),
    )
    if min(edges) < 0.80:
        return False
    t = max(2, int(round(0.18 * min(h, w))))
    strips = (
        float(ink[:t, :].mean()), float(ink[-t:, :].mean()),
        float(ink[:, :t].mean()), float(ink[:, -t:].mean()),
    )
    if min(strips) < 0.30:
        return False
    if min(strips) / max(strips) < 0.65:
        return False
    center = ink[int(0.30 * h):int(0.70 * h), int(0.30 * w):int(0.70 * w)]
    return float(center.mean()) <= 0.35


def _checkbox_is_checked(crop: np.ndarray) -> bool:
    """Ô tick có dấu bên trong? (dấu √ có thể là chữ hoặc nét vẽ)."""
    ink = (crop < 128).astype(np.uint8)
    h, w = ink.shape
    m = max(2, int(round(0.22 * min(h, w))))
    inner = ink[m:h - m, m:w - m]
    return inner.size > 0 and float(inner.mean()) > 0.02


def vector_glyph_items(
    page, img: np.ndarray, scale: float, existing: list[TextItem] | None = None
) -> list[TextItem]:
    """Các ký hiệu vector đoán được (ø, ô tick ☐/☑) -> danh sách TextItem."""
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

        crop = gray[iy0:iy1, ix0:ix1]
        box = (ix0, iy0, ix1, iy1)

        if (
            BOX_MIN_PT <= pw <= BOX_MAX_PT
            and BOX_MIN_PT <= ph <= BOX_MAX_PT
            and looks_like_checkbox(crop)
        ):
            if existing and (
                any(_covers_existing(box, it) for it in existing)
                or _covers_text_run(box, existing)
            ):
                continue                  # khung đã có trong text layer
            checked = _checkbox_is_checked(crop)
            out.append(
                TextItem(
                    x0=ix0, y0=iy0, x1=ix1, y1=iy1,
                    text=CHECKBOX_CHECKED if checked else CHECKBOX,
                    synthetic=True,
                )
            )
            continue

        if not looks_like_diameter(crop):
            continue

        if existing and any(_covers_existing(box, it) for it in existing):
            continue                      # ký hiệu đã có trong text layer
        if existing and _covers_text_run(box, existing):
            continue                      # path là cả cụm chữ vẽ bằng nét
        out.append(
            TextItem(
                x0=ix0, y0=iy0, x1=ix1, y1=iy1, text=DIAMETER, synthetic=True
            )
        )
    return out


def resolve_checkbox_marks(items: list[TextItem]) -> list[TextItem]:
    """Bỏ dấu √ nằm trong ô tick (đã thay bằng ☑) và đồng bộ trạng thái ô.

    File PDF thường để dấu √ là chữ trong text layer còn cái khung là nét vẽ.
    Nếu giữ cả hai, ô Excel ra `☑√样件` — thừa dấu. Hàm này bỏ dấu rời và đánh
    dấu ☑ cho khung chứa nó.
    """
    boxes = [it for it in items if it.synthetic and it.text in (CHECKBOX, CHECKBOX_CHECKED)]
    if not boxes:
        return items
    drop: set[int] = set()
    for i, it in enumerate(items):
        if it.synthetic or it.text.strip() not in CHECK_MARKS:
            continue
        for b in boxes:
            if b.x0 <= it.cx <= b.x1 and b.y0 <= it.cy <= b.y1:
                b.text = CHECKBOX_CHECKED
                drop.add(i)
                break
    if not drop:
        return items
    return [it for i, it in enumerate(items) if i not in drop]


def _covers_text_run(box, items: list[TextItem], min_items: int = 2) -> bool:
    """True nếu hộp bao chứa tâm của >= min_items chữ đã biết.

    Trường hợp PDF vẽ cả *cụm chữ* bằng nét (text ảo để tìm kiếm): path có hộp
    bao trùm nhiều ký tự, không phải một ký hiệu đơn lẻ.
    """
    n = 0
    for it in items:
        if box[0] <= it.cx <= box[2] and box[1] <= it.cy <= box[3]:
            n += 1
            if n >= min_items:
                return True
    return False


def _covers_existing(box, item: TextItem, ratio: float = 0.5) -> bool:
    """Ký hiệu vector trùng chỗ với chữ đã có -> bỏ (tránh nhân đôi)."""
    ix = min(box[2], item.x1) - max(box[0], item.x0)
    iy = min(box[3], item.y1) - max(box[1], item.y0)
    if ix <= 0 or iy <= 0:
        return False
    area = max(1, (box[2] - box[0]) * (box[3] - box[1]))
    return (ix * iy) / area > ratio
