"""Kiểu dữ liệu dùng chung cho toàn bộ pipeline."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class TextItem:
    """Một đơn vị chữ có toạ độ (ký tự của text layer hoặc một dòng của OCR).

    Toạ độ tính bằng pixel ảnh, gốc ở góc trên-trái (y hướng xuống).
    """

    x0: float
    y0: float
    x1: float
    y1: float
    text: str
    score: float | None = None

    @property
    def cx(self) -> float:
        return (self.x0 + self.x1) / 2.0

    @property
    def cy(self) -> float:
        return (self.y0 + self.y1) / 2.0

    @property
    def w(self) -> float:
        return self.x1 - self.x0

    @property
    def h(self) -> float:
        return self.y1 - self.y0


@dataclass
class TableCell:
    row: int
    col: int
    rowspan: int = 1
    colspan: int = 1
    text: str = ""
    score: float | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "r": self.row,
            "c": self.col,
            "rs": self.rowspan,
            "cs": self.colspan,
            "text": self.text,
        }
        if self.score is not None:
            d["score"] = round(self.score, 3)
        return d


@dataclass
class Table:
    page: int
    index: int
    num_rows: int
    num_cols: int
    cells: list[TableCell] = field(default_factory=list)
    engine: str = ""
    bbox: tuple[float, float, float, float] | None = None

    @property
    def id(self) -> str:
        return f"p{self.page}_t{self.index}"

    @property
    def title(self) -> str:
        return f"Trang {self.page} – Bảng {self.index}"

    def grid(self) -> list[list[str]]:
        """Lưới chuỗi kích thước num_rows x num_cols (ô bị gộp để trống)."""
        g = [["" for _ in range(self.num_cols)] for _ in range(self.num_rows)]
        for c in self.cells:
            if 0 <= c.row < self.num_rows and 0 <= c.col < self.num_cols:
                g[c.row][c.col] = c.text
        return g

    def cell_map(self) -> dict[tuple[int, int], TableCell]:
        return {(c.row, c.col): c for c in self.cells}

    def to_dict(self) -> dict[str, Any]:
        low = [
            {"r": c.row, "c": c.col, "score": round(c.score, 3)}
            for c in self.cells
            if c.score is not None and c.score < 0.75
        ]
        return {
            "id": self.id,
            "page": self.page,
            "index": self.index,
            "rows": self.num_rows,
            "cols": self.num_cols,
            "engine": self.engine,
            "title": self.title,
            "grid": self.grid(),
            "spans": [
                {"r": c.row, "c": c.col, "rs": c.rowspan, "cs": c.colspan}
                for c in self.cells
                if c.rowspan > 1 or c.colspan > 1
            ],
            "low_conf": low,
            "bbox": list(self.bbox) if self.bbox else None,
        }


@dataclass
class PageResult:
    page: int
    kind: str                 # "text" | "scan"
    used: str                 # engine thực tế đã dùng
    width_pt: float
    height_pt: float
    tables: list[Table] = field(default_factory=list)
    text: str = ""
    elapsed: float = 0.0
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "page": self.page,
            "kind": self.kind,
            "used": self.used,
            "width_pt": round(self.width_pt, 1),
            "height_pt": round(self.height_pt, 1),
            "elapsed": round(self.elapsed, 2),
            "warnings": self.warnings,
            "chars": len(self.text.strip()),
            "tables": [t.to_dict() for t in self.tables],
        }


@dataclass
class DocResult:
    filename: str
    pages: list[PageResult] = field(default_factory=list)
    duration: float = 0.0
    warnings: list[str] = field(default_factory=list)

    @property
    def tables(self) -> list[Table]:
        return [t for p in self.pages for t in p.tables]

    def to_dict(self) -> dict[str, Any]:
        return {
            "filename": self.filename,
            "duration": round(self.duration, 2),
            "num_pages": len(self.pages),
            "num_tables": len(self.tables),
            "warnings": self.warnings,
            "pages": [p.to_dict() for p in self.pages],
            "tables": [t.to_dict() for t in self.tables],
        }
