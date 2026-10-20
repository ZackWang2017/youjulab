"""Prompts stay bounded: model selection never receives a full PDF."""
from __future__ import annotations

SELECTOR = """You classify a native-text Chinese PDF layout using only its aggregate profile and a small representative layout sample. Return JSON only: {document_type:string,rule_pack:string,reason:string}. Select a conservative document_type from annual_report, interim_report, announcement, prospectus, generic, and select rule_pack only from allowed_rule_packs supplied by the user. Never return rules, regular expressions, code, or any inferred unseen text."""
