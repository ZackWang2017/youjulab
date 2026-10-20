"""Versioned, editable rule-pack loading for announcement outlines."""
from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from .schema import validate_rules

RULE_PACKS_FILE = Path(__file__).with_name("rule_packs.json")


def _config() -> dict[str, Any]:
    """Load on demand so rule-pack edits take effect without a service restart."""
    try:
        value = json.loads(RULE_PACKS_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"公告大纲规则配置无效：{exc}") from exc
    if not isinstance(value, dict) or not isinstance(value.get("defaults"), dict) or not isinstance(value.get("rule_packs"), dict):
        raise ValueError("公告大纲规则配置缺少 defaults 或 rule_packs。")
    return value


def available_rule_packs() -> set[str]:
    return set(_config()["rule_packs"])


def rule_pack_for(document_type: str, requested_pack: str = "") -> tuple[str, dict[str, Any]]:
    config = _config()
    mapped = str(config.get("document_type_packs", {}).get(document_type) or "generic")
    name = requested_pack if requested_pack in available_rule_packs() else mapped
    if name not in available_rule_packs():
        name = "generic"
    rules = deepcopy(config["defaults"])
    rules.update(config["rule_packs"][name])
    return name, validate_rules(rules)
