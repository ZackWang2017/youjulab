"""把用户访问令牌写入外部 Agent skill 模板。"""
from __future__ import annotations

from config import ROOT, office_api_base

PLACEHOLDER = "__ACCESS_TOKEN__"
OFFICE_BASE_PLACEHOLDER = "__OFFICE_BASE_URL__"
# 兼容旧模板占位符；渲染时一并替换为 office 基址。
LEGACY_MCP_BASE_PLACEHOLDER = "__MCP_BASE_URL__"
SKILLS = (
    {
        "id": "office",
        "name": "外部 Agent Skill",
        "filename": "youjulab-skill.md",
        "path": "youju-skills/SKILL.md",
    },
)


def list_skills() -> list[dict]:
    return [{"id": item["id"], "name": item["name"], "filename": item["filename"]} for item in SKILLS]


def render_skill(skill_id: str, access_token: str) -> tuple[str, str]:
    spec = next((item for item in SKILLS if item["id"] == skill_id), None)
    if not spec:
        raise KeyError(skill_id)
    text = (ROOT / spec["path"]).read_text(encoding="utf-8")
    if PLACEHOLDER not in text:
        raise RuntimeError("skill 模板缺少令牌占位符。")
    if OFFICE_BASE_PLACEHOLDER not in text and LEGACY_MCP_BASE_PLACEHOLDER not in text:
        raise RuntimeError("skill 模板缺少 office 地址占位符。")
    base = office_api_base()
    rendered = (
        text.replace(PLACEHOLDER, access_token)
        .replace(OFFICE_BASE_PLACEHOLDER, base)
        .replace(LEGACY_MCP_BASE_PLACEHOLDER, base)
    )
    return rendered, spec["filename"]
