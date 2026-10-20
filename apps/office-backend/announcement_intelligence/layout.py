"""Native PDF layout helpers shared by rules and model prompts."""
from __future__ import annotations

import re
from collections import Counter
from typing import Iterable

from .schema import LayoutBlock


def clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def remove_running_headers(blocks: Iterable[LayoutBlock]) -> list[LayoutBlock]:
    rows = list(blocks)
    repeated = Counter(clean_text(item.text) for item in rows if item.y_ratio < 0.08 or item.y_ratio > 0.92)
    return [item for item in rows if not ((item.y_ratio < 0.08 or item.y_ratio > 0.92) and repeated[clean_text(item.text)] >= 3)]


def document_profile(blocks: list[LayoutBlock]) -> dict:
    pages = {item.page for item in blocks}
    sizes = sorted(item.font_size for item in blocks if item.font_size > 0)
    return {
        "page_count": len(pages),
        "line_count": len(blocks),
        "bold_count": sum(1 for item in blocks if item.bold),
        "font_sizes": sorted({round(value, 1) for value in sizes})[:24],
    }


def compact_sample(blocks: list[LayoutBlock], limit: int = 72) -> list[dict]:
    """Representative layout evidence only; the model never receives full PDF text."""
    if len(blocks) <= 12:
        return [item.compact() for item in blocks]
    selected: list[LayoutBlock] = []
    per_page: dict[int, list[LayoutBlock]] = {}
    for block in blocks:
        per_page.setdefault(block.page, []).append(block)
    for page_rows in per_page.values():
        selected.append(page_rows[0])
        selected.extend(sorted(page_rows, key=lambda item: (item.bold, item.font_size), reverse=True)[:1])
    seen: set[str] = set()
    unique = [item for item in selected if not (item.id in seen or seen.add(item.id))]
    return [item.compact() for item in unique[:limit]]
