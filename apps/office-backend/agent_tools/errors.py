"""与历史 mcp OfficeError 兼容的业务错误。"""
from __future__ import annotations


class OfficeError(Exception):
    def __init__(self, status: int, message: str):
        self.status = status
        super().__init__(message)
