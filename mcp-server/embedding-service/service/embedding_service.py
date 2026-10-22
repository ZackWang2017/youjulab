"""把文本交给本机 embedding 提供方。调试默认 Ollama http://127.0.0.1:11434。"""
from __future__ import annotations

import os

import httpx


class EmbeddingError(Exception):
    def __init__(self, message: str, status: int = 502):
        self.status = status
        super().__init__(message)


def base_url() -> str:
    # 调试：本机 Ollama。模型改放到服务器或云厂商时，只改 EMBEDDING_BASE_URL。
    return (os.environ.get("EMBEDDING_BASE_URL") or "http://127.0.0.1:11434").strip().rstrip("/")


def model_name() -> str:
    return (os.environ.get("EMBEDDING_MODEL") or os.environ.get("OFFICE_EMBEDDING_MODEL") or "bge-m3").strip() or "bge-m3"


def dimensions() -> int:
    raw = (os.environ.get("EMBEDDING_DIM") or os.environ.get("OFFICE_EMBEDDING_DIM") or "1024").strip()
    return int(raw or "1024")


def embed_texts(texts: list[str]) -> list[list[float]]:
    if not texts:
        return []
    dim = dimensions()
    model = model_name()
    timeout = httpx.Timeout(30.0, read=180.0)
    out: list[list[float]] = []
    with httpx.Client(timeout=timeout) as client:
        for text in texts:
            res = client.post(
                f"{base_url()}/api/embeddings",
                json={"model": model, "prompt": text or " "},
            )
            if res.is_error:
                raise EmbeddingError(f"embedding HTTP {res.status_code}：{res.text[:300]}")
            vec = res.json().get("embedding")
            if not isinstance(vec, list) or len(vec) != dim:
                got = len(vec) if isinstance(vec, list) else 0
                raise EmbeddingError(f"embedding 维度为 {got}，期望 {dim}。")
            out.append([float(item) for item in vec])
    return out
