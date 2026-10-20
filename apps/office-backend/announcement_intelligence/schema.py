"""Small, serialisable contracts for the announcement-outline pipeline."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class LayoutBlock:
    id: str
    page: int
    order: int
    text: str
    font_size: float
    bold: bool
    x0: float
    y0: float
    x1: float
    y1: float
    indent: float
    y_ratio: float

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "LayoutBlock":
        return cls(
            id=str(row["block_id"]), page=int(row["page"]), order=int(row["ord"]), text=str(row["text"]),
            font_size=float(row.get("font_size") or 0), bold=bool(row.get("bold")),
            x0=float(row.get("x0") or 0), y0=float(row.get("y0") or 0),
            x1=float(row.get("x1") or 0), y1=float(row.get("y1") or 0),
            indent=float(row.get("indent") or 0), y_ratio=float(row.get("y_ratio") or 0),
        )

    def compact(self) -> dict[str, Any]:
        return {"id": self.id, "p": self.page, "o": self.order, "t": self.text, "s": round(self.font_size, 1), "b": self.bold, "y": round(self.y_ratio, 3), "i": round(self.indent, 1)}


ALLOWED_RULE_FIELDS = frozenset({
    "numbered", "bold", "min_font_percentile", "max_length", "unnumbered_max_length", "numbered_max_length", "numbered_max_punctuation",
    "min_length", "top_bottom_margin", "max_level", "max_punctuation", "max_digit_ratio",
    "font_size_min_delta", "bold_min_vertical_gap", "bold_max_width_ratio", "bold_max_same_line_blocks",
    "number_patterns", "number_level_patterns", "exclude_patterns", "body_terminal_patterns", "body_lead_patterns",
    "table_like_patterns",
})
BOOLEAN_RULE_FIELDS = frozenset({"numbered", "bold"})
NUMBER_RULE_FIELDS = frozenset({
    "min_font_percentile", "max_length", "unnumbered_max_length", "numbered_max_length", "numbered_max_punctuation", "min_length",
    "top_bottom_margin", "max_level", "max_punctuation", "max_digit_ratio", "font_size_min_delta",
    "bold_min_vertical_gap", "bold_max_width_ratio", "bold_max_same_line_blocks",
})
LIST_RULE_FIELDS = frozenset({"number_patterns", "exclude_patterns", "body_terminal_patterns", "body_lead_patterns", "table_like_patterns"})


def validate_rules(value: Any) -> dict[str, Any]:
    """Validate configured data only; no executable expressions are accepted."""
    if not isinstance(value, dict):
        raise ValueError("规则格式无效。")
    for key, raw in value.items():
        if key not in ALLOWED_RULE_FIELDS:
            raise ValueError(f"不支持的规则字段：{key}。")
        if key in BOOLEAN_RULE_FIELDS:
            if not isinstance(raw, bool):
                raise ValueError(f"{key} 必须是布尔值。")
        elif key in NUMBER_RULE_FIELDS:
            if not isinstance(raw, (int, float)):
                raise ValueError(f"{key} 必须是数字。")
        elif key in LIST_RULE_FIELDS:
            if not isinstance(raw, list) or len(raw) > 30 or not all(isinstance(item, str) and len(item) <= 180 for item in raw):
                raise ValueError(f"{key} 规则格式无效。")
        elif key == "number_level_patterns":
            if not isinstance(raw, list) or len(raw) > 24:
                raise ValueError("编号层级规则格式无效。")
            for item in raw:
                if not isinstance(item, dict) or not isinstance(item.get("pattern"), str) or len(item["pattern"]) > 180 or not isinstance(item.get("level"), int):
                    raise ValueError("编号层级规则格式无效。")
    missing = ALLOWED_RULE_FIELDS - set(value)
    if missing:
        raise ValueError(f"规则配置缺少字段：{', '.join(sorted(missing))}。")
    clean = dict(value)
    clean["min_font_percentile"] = max(50, min(99, int(clean["min_font_percentile"])))
    clean["top_bottom_margin"] = max(0, min(0.2, float(clean["top_bottom_margin"])))
    clean["max_length"] = max(12, min(180, int(clean["max_length"])))
    clean["unnumbered_max_length"] = max(8, min(clean["max_length"], int(clean["unnumbered_max_length"])))
    clean["numbered_max_length"] = max(8, min(clean["max_length"], int(clean["numbered_max_length"])))
    clean["numbered_max_punctuation"] = max(clean["max_punctuation"], min(12, int(clean["numbered_max_punctuation"])))
    clean["min_length"] = min(clean["max_length"], max(1, min(20, int(clean["min_length"]))))
    clean["max_level"] = max(1, min(6, int(clean["max_level"])))
    clean["max_punctuation"] = max(0, min(8, int(clean["max_punctuation"])))
    clean["max_digit_ratio"] = max(0.05, min(1.0, float(clean["max_digit_ratio"])))
    clean["font_size_min_delta"] = max(0.0, min(12.0, float(clean["font_size_min_delta"])))
    clean["bold_min_vertical_gap"] = max(0.0, min(80.0, float(clean["bold_min_vertical_gap"])))
    clean["bold_max_width_ratio"] = max(0.1, min(1.0, float(clean["bold_max_width_ratio"])))
    clean["bold_max_same_line_blocks"] = max(1, min(8, int(clean["bold_max_same_line_blocks"])))
    return clean
