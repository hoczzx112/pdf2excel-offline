"""Render trang PDF ra ảnh + tiện ích ảnh."""
from __future__ import annotations

import cv2
import numpy as np
import pypdfium2 as pdfium


def open_pdf(path) -> pdfium.PdfDocument:
    return pdfium.PdfDocument(str(path))


def render_page(page, dpi: int) -> np.ndarray:
    """Render 1 trang -> ảnh RGB (uint8, HxWx3)."""
    scale = dpi / 72.0
    bitmap = page.render(scale=scale)
    arr = bitmap.to_numpy()
    if arr.ndim == 2:
        arr = cv2.cvtColor(arr, cv2.COLOR_GRAY2RGB)
    elif arr.shape[2] == 4:
        arr = cv2.cvtColor(arr, cv2.COLOR_RGBA2RGB)
    return np.ascontiguousarray(arr)


def to_gray(img: np.ndarray) -> np.ndarray:
    if img.ndim == 3:
        return cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
    return img


def estimate_skew(binary: np.ndarray) -> float:
    """Ước lượng góc nghiêng (độ) từ các đường kẻ dài."""
    h, w = binary.shape[:2]
    best = 0.0
    lines = cv2.HoughLinesP(
        binary, 1, np.pi / 180, threshold=int(w * 0.15),
        minLineLength=int(w * 0.3), maxLineGap=6,
    )
    if lines is None:
        return 0.0
    angles = []
    for x1, y1, x2, y2 in np.asarray(lines).reshape(-1, 4):
        if x2 == x1:
            continue
        a = np.degrees(np.arctan2(y2 - y1, x2 - x1))
        if abs(a) <= 5:
            angles.append(a)
    if len(angles) >= 3:
        best = float(np.median(angles))
    return best


def deskew(img: np.ndarray, angle: float) -> np.ndarray:
    if abs(angle) < 0.25:
        return img
    h, w = img.shape[:2]
    m = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
    return cv2.warpAffine(
        img, m, (w, h),
        flags=cv2.INTER_CUBIC,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=(255, 255, 255),
    )


def save_png(img: np.ndarray, path, max_width: int = 1400) -> None:
    out = img
    if img.shape[1] > max_width:
        r = max_width / img.shape[1]
        out = cv2.resize(
            img, (max_width, int(img.shape[0] * r)), interpolation=cv2.INTER_AREA
        )
    cv2.imwrite(str(path), cv2.cvtColor(out, cv2.COLOR_RGB2BGR))
