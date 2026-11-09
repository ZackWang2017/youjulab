"""外部 Agent 办公工具路径判定。"""
from __future__ import annotations


def is_sheet_path(path: str) -> bool:
    return path == "/mcp/sheets" or path.startswith("/mcp/sheets/") or path.startswith("/api/spreadsheets")


def is_document_path(path: str) -> bool:
    return path == "/mcp/documents" or path.startswith("/mcp/documents/") or path.startswith("/api/documents")


def is_mindmap_path(path: str) -> bool:
    return path == "/mcp/mindmaps" or path.startswith("/mcp/mindmaps/") or path.startswith("/api/mindmaps")


def is_project_plan_path(path: str) -> bool:
    return (
        path == "/mcp/project-plans"
        or path.startswith("/mcp/project-plans/")
        or path.startswith("/api/project-plans")
    )


def is_user_office_path(path: str) -> bool:
    return is_sheet_path(path) or is_document_path(path) or is_mindmap_path(path) or is_project_plan_path(path) or is_diagram_path(path)


def is_diagram_path(path: str) -> bool:
    return path == '/mcp/diagrams' or path.startswith('/mcp/diagrams/') or path == '/api/diagrams' or path.startswith('/api/diagrams/')
