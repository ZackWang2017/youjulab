"""Deterministic heading rules. This module intentionally has no model dependency."""
from __future__ import annotations

import math
import re
from typing import Any

from .layout import remove_running_headers
from .schema import LayoutBlock, validate_rules


def _font_cutoff(blocks: list[LayoutBlock], percentile: int) -> float:
    sizes = sorted(item.font_size for item in blocks if item.font_size > 0)
    if not sizes:
        return 0
    index = min(len(sizes) - 1, max(0, math.ceil(len(sizes) * percentile / 100) - 1))
    return sizes[index]


def _body_font_size(blocks: list[LayoutBlock]) -> float:
    sizes = [round(item.font_size, 1) for item in blocks if item.font_size > 0]
    if not sizes:
        return 0
    return max(set(sizes), key=sizes.count)


def _layout_context(blocks: list[LayoutBlock]) -> dict[str, dict[str, float]]:
    """Summarise line spacing and width from PDF geometry without using text content."""
    by_page: dict[int, list[LayoutBlock]] = {}
    for block in blocks:
        by_page.setdefault(block.page, []).append(block)
    context: dict[str, dict[str, float]] = {}
    for page_blocks in by_page.values():
        max_width = max((item.x1 - item.x0 for item in page_blocks), default=1.0)
        lines: list[list[LayoutBlock]] = []
        for block in sorted(page_blocks, key=lambda item: (item.y0, item.x0)):
            if lines and abs(block.y0 - lines[-1][0].y0) <= 3:
                lines[-1].append(block)
            else:
                lines.append([block])
        for index, line in enumerate(lines):
            before = float("inf") if index == 0 else min(item.y0 for item in line) - max(item.y1 for item in lines[index - 1])
            after = float("inf") if index == len(lines) - 1 else min(item.y0 for item in lines[index + 1]) - max(item.y1 for item in line)
            for block in line:
                context[block.id] = {
                    "before_gap": before,
                    "after_gap": after,
                    "same_line_blocks": float(len(line)),
                    "width_ratio": (block.x1 - block.x0) / max_width,
                }
    return context


def _number_level(text: str, level_patterns: list[dict[str, Any]]) -> int | None:
    for item in level_patterns:
        if re.search(item["pattern"], text):
            return int(item["level"])
    return None


def _is_title_like(text: str, rules: dict[str, Any], *, marked: bool = False) -> bool:
    if len(text) < rules["min_length"] or len(text) > rules["max_length"]:
        return False
    if any(re.search(pattern, text, re.IGNORECASE) for pattern in rules["exclude_patterns"]):
        return False
    if any(re.search(pattern, text) for pattern in rules["table_like_patterns"]):
        return False
    if not marked and any(re.search(pattern, text) for pattern in rules["body_terminal_patterns"]):
        return False
    if any(re.search(pattern, text, re.IGNORECASE) for pattern in rules["body_lead_patterns"]):
        return False
    punctuation = len(re.findall(r"[，,；;。！？!?、]", text))
    if punctuation > (rules["numbered_max_punctuation"] if marked else rules["max_punctuation"]):
        return False
    visible = [char for char in text if not char.isspace()]
    digit_ratio = sum(char.isdigit() for char in visible) / max(1, len(visible))
    return digit_ratio <= rules["max_digit_ratio"]


def _has_bold_heading_layout(block: LayoutBlock, context: dict[str, float], rules: dict[str, Any]) -> bool:
    return (
        context.get("same_line_blocks", 1) <= rules["bold_max_same_line_blocks"]
        and context.get("width_ratio", 0) <= rules["bold_max_width_ratio"]
        and context.get("before_gap", float("inf")) >= rules["bold_min_vertical_gap"]
        and context.get("after_gap", float("inf")) >= rules["bold_min_vertical_gap"]
    )


def apply_rules(blocks: list[LayoutBlock], raw_rules: dict[str, Any]) -> list[dict[str, Any]]:
    rules = validate_rules(raw_rules)
    usable = remove_running_headers(blocks)
    cutoff = _font_cutoff(usable, rules["min_font_percentile"])
    body_font_size = _body_font_size(usable)
    contexts = _layout_context(usable)
    patterns = [re.compile(item, re.IGNORECASE) for item in rules["number_patterns"]]
    items: list[dict[str, Any]] = []
    for block in usable:
        text = block.text.strip()
        if block.y_ratio <= rules["top_bottom_margin"] or block.y_ratio >= 1 - rules["top_bottom_margin"]:
            continue
        level = _number_level(text, rules["number_level_patterns"])
        numbered = rules["numbered"] and len(text) <= rules["numbered_max_length"] and (any(pattern.search(text) for pattern in patterns) or level is not None)
        if not _is_title_like(text, rules, marked=numbered):
            continue
        bold_heading = block.bold and _has_bold_heading_layout(block, contexts.get(block.id, {}), rules)
        large_heading = block.font_size >= max(cutoff, body_font_size + rules["font_size_min_delta"])
        typographic = rules["bold"] and len(text) <= rules["unnumbered_max_length"] and (bold_heading or large_heading)
        if not numbered and not typographic:
            continue
        level = level or (1 if block.font_size >= cutoff else 2)
        items.append({"id": block.id, "page": block.page, "order": block.order, "title": text, "kind": "numbered" if numbered else "bold", "level": min(rules["max_level"], level)})
    out: list[dict[str, Any]] = []
    previous = ""
    for item in sorted(items, key=lambda row: row["order"]):
        if item["title"] == previous:
            continue
        out.append(item)
        previous = item["title"]
    return out
