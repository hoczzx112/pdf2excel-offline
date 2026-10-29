"""Cấu hình chung (có thể override bằng biến môi trường)."""
from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent          # ...\pdf2excel
DATA_DIR = Path(os.environ.get("PDF2XLSX_DATA", BASE_DIR / "data"))
JOBS_DIR = DATA_DIR / "jobs"
LOG_DIR = DATA_DIR / "logs"
WEB_DIR = BASE_DIR / "app" / "web"

JOBS_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)

# --- upload ---
MAX_UPLOAD_MB = int(os.environ.get("PDF2XLSX_MAX_MB", "100"))
MAX_FILES = int(os.environ.get("PDF2XLSX_MAX_FILES", "20"))

# --- pipeline ---
DPI = int(os.environ.get("PDF2XLSX_DPI", "200"))            # render để dò lưới / OCR
MAX_PAGES = int(os.environ.get("PDF2XLSX_MAX_PAGES", "60"))

# dò đường kẻ
LINE_MIN_RATIO = 0.05          # đường kẻ phải dài >= 5% chiều rộng/ cao trang
LINE_GROUP_GAP = 5             # gộp các toạ độ sát nhau (px)
BORDER_PRESENCE = 0.55         # tỉ lệ pixel tối để coi là "có đường kẻ"

# gộp ô thành bảng
TABLE_JOIN_GAP = 0.012         # khoảng hở tối đa (theo chiều cao trang) để coi là cùng bảng
MIN_TABLE_CELLS = 3

# OCR
OCR_MIN_SCORE = 0.30           # bỏ kết quả OCR quá kém
OCR_WARN_SCORE = 0.75          # tô vàng các ô dưới ngưỡng này

HOST = os.environ.get("PDF2XLSX_HOST", "127.0.0.1")
PORT = int(os.environ.get("PDF2XLSX_PORT", "8765"))
