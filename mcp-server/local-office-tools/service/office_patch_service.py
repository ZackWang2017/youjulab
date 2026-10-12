"""Create selection-bound proposals and reject stale or malformed edits."""
from __future__ import annotations

from uuid import uuid4

from office_context_service import OfficeContext


def create_patch(context: OfficeContext, replacement) -> dict:
    if context.surface == "pdf":
        raise ValueError("PDF is read-only in this MVP")
    s = context.selection
    if context.surface == "sheet":
        if not isinstance(replacement, list) or len(replacement) != s["rows"]:
            raise ValueError("Replacement must preserve selected row count")
        check = context.model_dump()
        check["selection"] = {**s, "values": replacement}
        OfficeContext.model_validate(check)
    elif context.surface == "mindmap":
        if not isinstance(replacement, str) or len(replacement) > 40000:
            raise ValueError("Replacement must be plain text under 40000 characters")
    elif context.surface == "calendar":
        if not isinstance(replacement, str) or len(replacement) > 40000:
            raise ValueError("Replacement must be Markdown under 40000 characters")
    elif not isinstance(replacement, str) or len(replacement) > 40000:
        raise ValueError("Replacement must be text under 40000 characters")
    return {**context.model_dump(exclude={"surrounding"}), "patch_id": uuid4().hex,
            "replacement": replacement}


def validate_office_patch(patch: dict, current_context: dict) -> dict:
    """Reject proposals whose source document, revision, or selection changed."""
    current = OfficeContext.model_validate(current_context)
    for key in ("document_id", "surface", "revision", "selection"):
        if patch.get(key) != getattr(current, key):
            raise ValueError("Document or selection changed; generate a new proposal")
    create_patch(current, patch.get("replacement"))
    return {"valid": True, "patch_id": patch.get("patch_id")}
