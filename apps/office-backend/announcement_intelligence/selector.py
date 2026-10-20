"""Validation for the model-selected document profile and configured rule pack."""
from __future__ import annotations

from typing import Any

from .rule_config import rule_pack_for

_TYPES = frozenset({"annual_report", "interim_report", "announcement", "prospectus", "generic"})


def selection_from_model(value: Any) -> tuple[str, str, dict[str, Any], str]:
    if not isinstance(value, dict):
        raise ValueError("模型未返回规则选择。")
    document_type = str(value.get("document_type") or "generic").strip().lower()
    if document_type not in _TYPES:
        document_type = "generic"
    rule_pack, rules = rule_pack_for(document_type, str(value.get("rule_pack") or "").strip())
    return document_type, rule_pack, rules, str(value.get("reason") or "").strip()[:240]
