"""把用户访问令牌写入外部 Agent skill 模板。"""
from __future__ import annotations

from config import ROOT

PLACEHOLDER = "__ACCESS_TOKEN__"
SKILLS = (
    {
        "id": "chatgpt",
        "name": "ChatGPT",
        "filename": "invp-skills-chatgpt.md",
        "path": "invp-skills/invp-skills-chatgpt/SKILL.md",
    },
    {
        "id": "workbuddy",
        "name": "WorkBuddy",
        "filename": "invp-skills-workbuddy.md",
        "path": "invp-skills/invp-skills-workbuddy/SKILL.md",
    },
    {
        "id": "deepseek",
        "name": "DeepSeek",
        "filename": "invp-skills-deepseek.md",
        "path": "invp-skills/invp-skills-deepseek/SKILL.md",
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
    return text.replace(PLACEHOLDER, access_token), spec["filename"]
