"""pdf_ask tool-call 循环（mock 模型）。"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))

from pdf_ask import iter_pdf_ask_events  # noqa: E402


class PdfAskToolLoopTests(unittest.IsolatedAsyncioTestCase):
    async def test_tool_then_answer(self):
        calls = {"n": 0}

        async def fake_chat(messages, *, json_object=False, tools=None, stream=True, tool_choice=None, thinking=False):
            calls["n"] += 1
            if tools and not stream:
                if calls["n"] == 1:
                    yield {
                        "type": "message",
                        "message": {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [{
                                "id": "call_1",
                                "type": "function",
                                "function": {
                                    "name": "search_office_pdf",
                                    "arguments": json.dumps({
                                        "document_id": "doc1",
                                        "query": "revenue Europe",
                                        "top_k": 3,
                                    }),
                                },
                            }],
                        },
                    }
                    return
                yield {
                    "type": "message",
                    "message": {"role": "assistant", "content": "根据第 2 页，欧洲收入增长。"},
                }
                return
            if stream:
                yield {"type": "delta", "text": "根据第 2 页，欧洲收入增长。"}

        with patch("pdf_ask.iter_chat_events", fake_chat), \
             patch("pdf_ask.ensure_index", return_value={"status": "ready", "page_count": 2, "chunk_count": 2}), \
             patch("pdf_ask.search_chunks", return_value=[{"page": 2, "text": "Europe revenue", "score": 0.9}]), \
             patch("pdf_ask.model_configured", return_value=True), \
             patch("pdf_ask.model_name", return_value="mock"):
            events = []
            async for event in iter_pdf_ask_events(
                selection="Alpha revenue",
                question="请结合本文档说明欧洲收入",
                page=1,
                document_id="doc1",
                user_id="u1",
                path="/tmp/x.pdf",
                filename="a.pdf",
                history=[],
            ):
                events.append(event)
        types = [e["type"] for e in events]
        self.assertIn("index", types)
        self.assertIn("delta", types)
        self.assertIn("done", types)
        done = next(e for e in events if e["type"] == "done")
        self.assertIn("欧洲", done["answer"])

    async def test_compose_includes_reasoning(self):
        from pdf_ask import compose_answer
        text = compose_answer("先看第2页证据。", "欧洲收入增长。")
        self.assertNotIn("思考过程", text)
        self.assertEqual(text, "欧洲收入增长。")

    def test_ask_mode_routes(self):
        from pdf_ask import ask_mode
        self.assertEqual(ask_mode("商誉是什么，会计上如何核算"), "concept")
        self.assertEqual(ask_mode("请分析收入下滑的原因"), "analysis")
        self.assertEqual(ask_mode("讨论一下本文档里的现金流"), "document")
        self.assertEqual(ask_mode("2023年年报的毛利率为什么下降"), "document")

    def test_omit_empty_search_sections(self):
        from pdf_ask import omit_empty_search_sections
        text = omit_empty_search_sections(
            "## 百度百科\n\n无\n\n## 百度搜索\n\n无\n\n## Google搜索\n\n无\n\n"
            "## 模型搜索\n\n商誉是企业合并成本超过可辨认净资产公允价值的差额。\n\n"
            "## 总结\n\n按权益法不逐期摊销。"
        )
        self.assertNotIn("百度百科", text)
        self.assertNotIn("百度搜索", text)
        self.assertNotIn("Google搜索", text)
        self.assertIn("模型搜索", text)
        self.assertIn("总结", text)
        filled = omit_empty_search_sections(
            "## 百度百科\n\n[商誉](https://baike.baidu.com/item/商誉)：合并差额。\n\n"
            "## 模型搜索\n\n不应出现。\n\n## 总结\n\n见百科。"
        )
        self.assertIn("百度百科", filled)
        self.assertNotIn("模型搜索", filled)


if __name__ == "__main__":
    unittest.main()
