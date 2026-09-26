"""Validated, bounded snapshots of a browser-owned office selection."""
from __future__ import annotations

import json
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class OfficeContext(BaseModel):
    model_config = ConfigDict(extra="forbid")
    document_id: str = Field(min_length=1, max_length=200)
    surface: Literal["sheet", "document", "pdf", "mindmap", "calendar"]
    revision: int = Field(ge=0)
    selection: dict
    surrounding: str = Field(default="", max_length=20000)

    @model_validator(mode="after")
    def check_selection(self):
        s = self.selection
        if len(json.dumps(s, ensure_ascii=False)) > 80000:
            raise ValueError("Selection is too large; select a smaller range")
        if self.surface == "sheet":
            for key in ("row", "column", "rows", "cols"):
                if type(s.get(key)) is not int or s[key] < (1 if key in ("rows", "cols") else 0):
                    raise ValueError(f"Invalid sheet {key}")
            if not isinstance(s.get("sheet_id"), str) or s["rows"] * s["cols"] > 2000:
                raise ValueError("Invalid sheet id or selection exceeds 2000 cells")
            values = s.get("values")
            if not isinstance(values, list) or len(values) != s["rows"]:
                raise ValueError("Invalid matrix height")
            for row in values:
                if not isinstance(row, list) or len(row) != s["cols"]:
                    raise ValueError("Invalid matrix width")
                if any(v is not None and type(v) not in (str, int, float, bool) for v in row):
                    raise ValueError("Invalid cell value")
        elif self.surface == "mindmap":
            if not isinstance(s.get("id"), str) or not s["id"].strip():
                raise ValueError("Select a mind-map node first")
            if not isinstance(s.get("text"), str):
                raise ValueError("Invalid mind-map node text")
            if type(s.get("is_root")) is not bool:
                raise ValueError("Missing is_root")
            path = s.get("path")
            if not isinstance(path, list) or any(not isinstance(p, str) for p in path):
                raise ValueError("Invalid mind-map path")
            parent_id = s.get("parent_id")
            if parent_id is not None and (not isinstance(parent_id, str) or not parent_id.strip()):
                raise ValueError("Invalid parent_id")
        elif self.surface == "calendar":
            date = s.get("date")
            if not isinstance(date, str) or not _DATE.match(date):
                raise ValueError("Select a date first")
            if not isinstance(s.get("text"), str):
                raise ValueError("Invalid day plan text")
            if len(s["text"]) > 40000:
                raise ValueError("Day plan text is too long")
        else:
            if not isinstance(s.get("text"), str) or not s["text"].strip():
                raise ValueError("Select text first")
            if self.surface == "document":
                if type(s.get("from")) is not int or type(s.get("to")) is not int or not 0 <= s["from"] < s["to"]:
                    raise ValueError("Invalid text positions")
                if type(s.get("single_block")) is not bool:
                    raise ValueError("Missing textblock boundary information")
            elif type(s.get("page")) is not int or s["page"] < 1:
                raise ValueError("Invalid PDF page")
        return self


def get_office_context(context: dict) -> dict:
    """Validate an editor snapshot; does not read hidden browser state."""
    return OfficeContext.model_validate(context).model_dump()


def register_tools(mcp):
    mcp.tool()(get_office_context)
