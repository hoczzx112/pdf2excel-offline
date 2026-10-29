"""Wrapper cho RapidOCR (ONNX Runtime nếu có, không thì PyTorch)."""
from __future__ import annotations

import threading

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
    return items


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
