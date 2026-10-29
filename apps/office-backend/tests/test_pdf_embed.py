"""PDF embedding 客户端应遵守远程服务的单次 256 条限制。"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import office_pdf_embed as embed  # noqa: E402


class _Response:
    status_code = 200
    is_error = False
    text = ""

    def __init__(self, texts: list[str]) -> None:
        self._texts = texts

    def json(self):
        return {"data": [
            {"index": index, "embedding": [float(text.rsplit("-", 1)[1])] * 3}
            for index, text in reversed(list(enumerate(self._texts)))
        ]}


class _Client:
    batches: list[list[str]] = []

    def __init__(self, **_kwargs) -> None:
        pass

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        pass

    def post(self, _url, *, headers, json):
        self.batches.append(json["input"])
        return _Response(json["input"])


class PdfEmbedTests(unittest.TestCase):
    def test_batches_requests_at_remote_limit(self) -> None:
        _Client.batches = []
        texts = [f"chunk-{index}" for index in range(257)]
        with patch.object(embed, "remote_token", return_value="token"), \
             patch.object(embed, "embedding_dim", return_value=3), \
             patch.object(embed.httpx, "Client", _Client):
            vectors = embed.embed_texts(texts)
        self.assertEqual([len(batch) for batch in _Client.batches], [256, 1])
        self.assertEqual(len(vectors), len(texts))
        self.assertEqual(vectors[0], [0.0, 0.0, 0.0])
        self.assertEqual(vectors[-1], [256.0, 256.0, 256.0])


if __name__ == "__main__":
    unittest.main()
