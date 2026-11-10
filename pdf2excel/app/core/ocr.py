"""Wrapper cho RapidOCR (ONNX Runtime nếu có, không thì PyTorch)."""
from __future__ import annotations

import threading
import unicodedata

import cv2
import numpy as np

from ..config import OCR_MIN_SCORE
from .models import TextItem

_lock = threading.Lock()
_engine = None
_engine_name = ""


def _build():
    from rapidocr import RapidOCR
    from rapidocr.utils.typings import EngineType

    params: dict = {"Global.log_level": "warning"}
    try:
        import onnxruntime  # noqa: F401
        engine_type = EngineType.ONNXRUNTIME
    except Exception:
        engine_type = EngineType.TORCH

    for sec in ("Det", "Cls", "Rec"):
        params[f"{sec}.engine_type"] = engine_type
    return RapidOCR(params=params), engine_type.value


def get_engine():
    global _engine, _engine_name
    with _lock:
        if _engine is None:
            _engine, _engine_name = _build()
    return _engine


def engine_name() -> str:
    get_engine()
    return _engine_name


def warmup() -> None:
    get_engine()


def ocr_items(img: np.ndarray) -> list[TextItem]:
    """Chạy OCR trên ảnh RGB, trả về các dòng chữ kèm bbox."""
    engine = get_engine()
    out = engine(img)
    txts = getattr(out, "txts", None) or []
    boxes = getattr(out, "boxes", None)
    scores = getattr(out, "scores", None) or []

    if boxes is None:
        return []

    # Ảnh nhị phân chỉ dùng khi có ký tự dễ nhầm với dấu "/" — bước này rẻ.
    binary = None
    if any(str(t).strip() in _SLASH_LIKE for t in txts):
        from .gridlines import to_binary

        binary = to_binary(img)

    items: list[TextItem] = []
    for i, txt in enumerate(txts):
        if not txt or not str(txt).strip():
            continue
        text = str(txt).strip()
        score = float(scores[i]) if i < len(scores) else None
        if score is not None and score < OCR_MIN_SCORE:
            continue
        pts = np.asarray(boxes[i], dtype=float).reshape(-1, 2)
        if pts.size < 8:
            continue
        x0 = float(pts[:, 0].min())
        y0 = float(pts[:, 1].min())
        x1 = float(pts[:, 0].max())
        y1 = float(pts[:, 1].max())

        # PP-OCR hay đọc dấu "/" thành "1" (nét chéo mảnh, ảnh rec chỉ cao
        # 48 px). Phân biệt bằng hình học: "/" là một nét thẳng chéo, còn "1"
        # có gạch đầu + chân nên không thẳng. Chỉ áp dụng cho ký tự đơn.
        if text in _SLASH_LIKE and binary is not None:
            crop = binary[max(0, int(y0)): int(y1) + 1, max(0, int(x0)): int(x1) + 1]
            if looks_like_slash(crop):
                text = "/"

        items.append(
            TextItem(x0=x0, y0=y0, x1=x1, y1=y1, text=text, score=score)
        )

    resolve_slashed_zeros(img, items)
    return items


_ZERO_LIKE = set("0OoQ")
_ZERO_SEPARATORS = set("-−~–×(Xx*/\\|,;:+=<>[]{}") | set("，、。；：（）【】《》")
_ZERO_DARK = 160


def _is_cjk(ch: str) -> bool:
    return bool(ch) and (
        unicodedata.east_asian_width(ch) in ("W", "F") or ord(ch) >= 0x2E80
    )


def _char_width(ch: str) -> float:
    """Độ rộng tương đối của ký tự (đơn vị = bề rộng chữ số)."""
    if ch in ".．":
        return 0.45
    if ch in "-−~–":
        return 0.55
    if ch == " ":
        return 0.5
    if _is_cjk(ch):
        return 2.0
    return 1.0


def _zero_candidates(text: str) -> list[int]:
    """Vị trí các chữ "0" có thể thật ra là ký hiệu ø."""
    out: list[int] = []
    for i, ch in enumerate(text):
        if ch not in _ZERO_LIKE:
            continue
        prev = text[i - 1] if i else ""
        if i == 0 or prev in _ZERO_SEPARATORS or _is_cjk(prev) or prev == " ":
            out.append(i)
    return out


def _hole_boxes(
    mask: np.ndarray, labels: np.ndarray
) -> list[tuple[int, float, float, int, int, int, int, int]]:
    """Các lỗ kín của nét mực: (area, cx, cy, x0, y0, x1, y1, nhãn khối mực)."""
    inv = np.pad((~mask).astype(np.uint8), 1, constant_values=1)
    lab = np.pad(labels, 1)
    n, _l, stats, cents = cv2.connectedComponentsWithStats(inv, 4)
    out = []
    for i in range(1, n):
        x, y, w, h, area = (int(v) for v in stats[i][:5])
        if area < 4:
            continue
        if x == 0 or y == 0 or x + w >= inv.shape[1] or y + h >= inv.shape[0]:
            continue
        # khối mực bao quanh lỗ (lấy nhãn xuất hiện nhiều nhất quanh viền lỗ)
        ring: list[int] = []
        for xx in range(max(0, x - 2), min(lab.shape[1], x + w + 2)):
            ring.append(int(lab[max(0, y - 2), xx]))
            ring.append(int(lab[min(lab.shape[0] - 1, y + h + 1), xx]))
        for yy in range(max(0, y - 2), min(lab.shape[0], y + h + 2)):
            ring.append(int(lab[yy, max(0, x - 2)]))
            ring.append(int(lab[yy, min(lab.shape[1] - 1, x + w + 1)]))
        ring = [v for v in ring if v > 0]
        outer = max(set(ring), key=ring.count) if ring else 0
        out.append((area, float(cents[i][0]) - 1.0, float(cents[i][1]) - 1.0,
                    x - 1, y - 1, x + w - 1, y + h - 1, outer))
    return out


def _zero_pairs(mask: np.ndarray, line_h: int) -> list[float]:
    """Tâm x của các ký hiệu ø: một lỗ tròn bị nét gạch chéo cắt làm hai.

    Chữ "0" chỉ có một lỗ, chữ "8" có hai lỗ nhưng hai lỗ nằm cách nhau
    (giữa là nền trắng). Ở ø, hai nửa lỗ **sát nhau**, chỉ cách nhau đúng bề
    dày nét gạch, và thuộc cùng một khối mực.
    """
    ink = mask.astype(np.uint8)
    _n, labels, _stats, _cents = cv2.connectedComponentsWithStats(ink, 8)
    min_area = max(4, 0.008 * line_h * line_h)
    holes = [p for p in _hole_boxes(mask, labels) if p[0] >= min_area]
    out: list[float] = []
    for a in range(len(holes)):
        for b in range(a + 1, len(holes)):
            aa, cxa, _cya, ax0, ay0, ax1, ay1, la = holes[a]
            ab, cxb, _cyb, bx0, by0, bx1, by1, lb = holes[b]
            if la != lb or la == 0:
                continue
            if min(aa, ab) / max(aa, ab) < 0.30:
                continue
            # hai nửa lỗ chỉ cách nhau bề dày nét gạch
            hw = min(ax1 - ax0, bx1 - bx0)
            hh = min(ay1 - ay0, by1 - by0)
            gx = max(0, max(ax0, bx0) - min(ax1, bx1))
            gy = max(0, max(ay0, by0) - min(ay1, by1))
            if gx > max(1.0, 0.15 * hw) or gy > max(1.0, 0.15 * hh):
                continue
            out.append((cxa + cxb) / 2.0)
    return out


def _ink_blocks(mask: np.ndarray) -> list[tuple[int, int]]:
    """Các cụm cột có mực (bỏ hàng là đường kẻ ngang), gộp cụm cách ≤ 1 px."""
    w = mask.shape[1]
    if w == 0:
        return []
    band = mask[mask.sum(axis=1) < 0.85 * w]
    if band.size == 0:
        return []
    cols = band.any(axis=0)
    blocks: list[tuple[int, int]] = []
    start: int | None = None
    for x, v in enumerate(cols):
        if v and start is None:
            start = x
        elif not v and start is not None:
            blocks.append((start, x))
            start = None
    if start is not None:
        blocks.append((start, len(cols)))
    merged: list[tuple[int, int]] = []
    for b in blocks:
        if merged and b[0] - merged[-1][1] <= 1:
            merged[-1] = (merged[-1][0], b[1])
        else:
            merged.append(b)
    return merged


def _fix_zeros(text: str, dark: np.ndarray, it: TextItem) -> str:
    """Thay các chữ "0" đúng vị trí cặp lỗ của ø (nếu có) trong `it`."""
    cands = _zero_candidates(text)
    if not cands:
        return text
    x0, y0 = max(0, int(it.x0) - 2), max(0, int(it.y0) - 2)
    x1 = min(dark.shape[1], int(it.x1) + 3)
    y1 = min(dark.shape[0], int(it.y1) + 3)
    sub = dark[y0:y1, x0:x1]
    ys, xs = np.nonzero(sub)
    if xs.size == 0:
        return text
    ix0, ix1 = int(xs.min()), int(xs.max()) + 1
    iy0, iy1 = int(ys.min()), int(ys.max()) + 1
    line_h = iy1 - iy0
    if line_h < 8:
        return text
    mask = sub[iy0:iy1]
    pairs = _zero_pairs(mask, line_h)
    if not pairs:
        return text

    total = sum(_char_width(c) for c in text)
    unit = (ix1 - ix0) / total if total else 0.0
    if unit <= 0:
        return text
    blocks = _ink_blocks(mask)
    out = list(text)
    for px in pairs:
        # số glyph nằm bên trái ký hiệu (đếm cụm mực) — dùng để phá thế hoà khi
        # chuỗi có ký tự rộng bất thường (★, dấu ngoặc) làm mô hình độ rộng lệch.
        left_blocks = sum(1 for b in blocks if (b[0] + b[1]) / 2.0 < px - 0.15 * unit)
        best, best_d, best_gap = -1, 1e9, 99
        for i in cands:
            left = sum(_char_width(c) for c in text[:i])
            half = _char_width(text[i]) / 2
            cl = ix0 + unit * (left + half)
            cr = ix1 - unit * (total - left - half)
            d = min(abs(cl - px), abs(cr - px)) / unit
            if d < best_d:
                best, best_d, best_gap = i, d, abs(i - left_blocks)
        ok = best >= 0 and (best_d <= 0.7 or (best_d <= 1.6 and best_gap <= 1))
        if ok and out[best] in _ZERO_LIKE:
            out[best] = "ø"
    return "".join(out)


def resolve_slashed_zeros(img: np.ndarray, items: list[TextItem]) -> None:
    """Sửa các chữ "0" mà OCR đọc nhầm từ ký hiệu đường kính ø (ảnh scan).

    RapidOCR thường đọc nét gạch chéo của ø thành chữ số: `ø129.5` -> `0129.5`,
    `8-ø3.9` -> `8-03.9`. Không thể phân biệt bằng chuỗi (kết quả OCR sai ổn
    định), nên phải nhìn ảnh: ø có **hai nửa lỗ sát nhau**, còn "0" chỉ một lỗ
    và "8" có hai lỗ nhưng cách xa nhau.
    """
    if not items:
        return
    gray = img if img.ndim == 2 else img.mean(axis=2)
    dark = np.asarray(gray) < _ZERO_DARK
    for it in items:
        if not any(ch in _ZERO_LIKE for ch in it.text):
            continue
        new = _fix_zeros(it.text, dark, it)
        if new != it.text:
            it.text = new


_SLASH_LIKE = {"1", "l", "I", "|"}


def looks_like_slash(crop: np.ndarray) -> bool:
    """True nếu nét mực trong ô là một đường chéo thẳng (dấu /).

    `crop` là mask nhị phân (nét > 0). Dấu "/" cho R² ≈ 0,99 và độ dốc
    dx/dy ≈ 0,24; chữ "1" có gạch đầu/chân nên R² thấp (< 0,5).
    """
    ys, xs = np.nonzero(crop)
    if xs.size < 15:
        return False
    rows: dict[int, list[int]] = {}
    for y, x in zip(ys.tolist(), xs.tolist()):
        rows.setdefault(y, []).append(x)
    yy = np.array(sorted(rows), dtype=float)
    if yy.size < 8:
        return False
    mx = np.array([sum(rows[int(y)]) / len(rows[int(y)]) for y in yy])
    slope, intercept = np.polyfit(yy, mx, 1)
    pred = slope * yy + intercept
    ss = float(((mx - mx.mean()) ** 2).sum())
    if ss < 1e-6:
        return False
    r2 = 1.0 - float(((mx - pred) ** 2).sum()) / ss
    return r2 > 0.95 and 0.10 <= abs(float(slope)) <= 0.60



def items_to_text(items: list[TextItem], line_tol: float = 8.0) -> str:
    """Nối các dòng OCR thành text theo thứ tự đọc."""
    if not items:
        return ""
    ordered = sorted(items, key=lambda i: (i.cy, i.x0))
    lines: list[list[TextItem]] = []
    cur = [ordered[0]]
    for it in ordered[1:]:
        if abs(it.cy - cur[-1].cy) <= line_tol:
            cur.append(it)
        else:
            lines.append(cur)
            cur = [it]
    lines.append(cur)
    return "\n".join(
        " ".join(i.text for i in sorted(ln, key=lambda x: x.x0)) for ln in lines
    )
