"""PDF 块向量：默认本地 Ollama bge-m3（DeepSeek 无官方 embeddings）。"""
from __future__ import annotations

import httpx

from config import (
    embedding_api_key,
    embedding_base_url,
    embedding_dim,
    embedding_model,
    embedding_provider,
)


class EmbedError(ValueError):
    pass


def embed_texts(texts: list[str]) -> list[list[float]]:
    if not texts:
        return []
    provider = embedding_provider()
    if provider == "ollama":
        return _ollama(texts)
    if provider in ("openai", "openai_compatible"):
        return _openai_compatible(texts)
    raise EmbedError(f"未知 embedding 提供方：{provider}")


def _ollama(texts: list[str]) -> list[list[float]]:
    base = embedding_base_url()
    model = embedding_model()
    dim = embedding_dim()
    out: list[list[float]] = []
    timeout = httpx.Timeout(30.0, read=180.0)
    with httpx.Client(timeout=timeout) as client:
        for text in texts:
            # Ollama /api/embeddings 一次一条
            res = client.post(f"{base}/api/embeddings", json={"model": model, "prompt": text or " "})
            if res.is_error:
                raise EmbedError(f"Ollama embedding HTTP {res.status_code}：{res.text[:300]}")
            data = res.json()
            vec = data.get("embedding")
            if not isinstance(vec, list) or not vec:
                raise EmbedError("Ollama 未返回 embedding。")
            if len(vec) != dim:
                raise EmbedError(f"embedding 维度为 {len(vec)}，期望 {dim}。")
            out.append([float(x) for x in vec])
    return out


def _openai_compatible(texts: list[str]) -> list[list[float]]:
    base = embedding_base_url()
    model = embedding_model()
    dim = embedding_dim()
    headers = {"Content-Type": "application/json"}
    key = embedding_api_key()
    if key:
        headers["Authorization"] = f"Bearer {key}"
    timeout = httpx.Timeout(30.0, read=180.0)
    with httpx.Client(timeout=timeout) as client:
        res = client.post(f"{base}/embeddings", headers=headers, json={"model": model, "input": texts})
        if res.is_error:
            raise EmbedError(f"Embedding HTTP {res.status_code}：{res.text[:300]}")
        data = res.json()
        items = data.get("data")
        if not isinstance(items, list) or len(items) != len(texts):
            raise EmbedError("Embedding 返回条数不匹配。")
        items = sorted(items, key=lambda item: item.get("index", 0))
        out: list[list[float]] = []
        for item in items:
            vec = item.get("embedding")
            if not isinstance(vec, list) or len(vec) != dim:
                raise EmbedError(f"embedding 维度异常，期望 {dim}。")
            out.append([float(x) for x in vec])
        return out
