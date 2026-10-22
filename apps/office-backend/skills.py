"""把用户访问令牌写入外部 Agent skill 模板。"""
from __future__ import annotations

from config import ROOT, mcp_local_base

PLACEHOLDER = "__ACCESS_TOKEN__"
MCP_BASE_PLACEHOLDER = "__MCP_BASE_URL__"
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
    if MCP_BASE_PLACEHOLDER not in text:
        raise RuntimeError("skill 模板缺少 MCP 地址占位符。")
    return (
        text.replace(PLACEHOLDER, access_token).replace(MCP_BASE_PLACEHOLDER, mcp_local_base()),
        spec["filename"],
    )
