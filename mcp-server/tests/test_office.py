import copy
import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "local-office-tools" / "service"))
sys.path.insert(0, str(_ROOT / "local-office-tools" / "repository"))

from office_agent_service import run_office_agent
from office_context_service import OfficeContext
from office_patch_service import create_patch, validate_office_patch


def sheet():
    return {"document_id": "book", "surface": "sheet", "revision": 3,
            "selection": {"sheet_id": "s1", "row": 0, "column": 0,
                          "rows": 1, "cols": 3, "values": [[" name ", 0, "=B1+1"]]}}


class OfficeTests(unittest.IsolatedAsyncioTestCase):
    async def test_demo_cleans_text_preserving_formula_and_zero(self):
        result = await run_office_agent(sheet(), "trim", "demo")
        self.assertEqual(result["patch"]["replacement"], [["name", 0, "=B1+1"]])
        self.assertTrue(validate_office_patch(result["patch"], sheet())["valid"])

    async def test_stale_and_cross_document_patches_rejected(self):
        result = await run_office_agent(sheet(), "trim")
        for key, value in (("revision", 4), ("document_id", "other")):
            context = {**sheet(), key: value}
            with self.assertRaises(ValueError):
                validate_office_patch(result["patch"], context)

    async def test_selection_change_rejected(self):
        result = await run_office_agent(sheet(), "trim")
        context = sheet()
        context["selection"]["values"][0][0] = "user edit"
        with self.assertRaises(ValueError):
            validate_office_patch(result["patch"], context)

    def test_malformed_matrix_and_invalid_scalar_rejected(self):
        for replacement in ([[1]], [[{}, 2, 3]], [[1, 2, 3], [4, 5, 6]]):
            with self.assertRaises(ValueError):
                create_patch(OfficeContext.model_validate(sheet()), replacement)

    async def test_pdf_quote_has_page_and_never_patch(self):
        context = {"document_id": "pdf", "surface": "pdf", "revision": 0,
                   "selection": {"text": "Revenue", "page": 2, "filename": "report.pdf"}}
        result = await run_office_agent(context, "explain")
        self.assertIn("第 2 页", result["answer"])
        self.assertIsNone(result["patch"])
        with self.assertRaises(ValueError):
            create_patch(OfficeContext.model_validate(context), "edit")

    async def test_model_mode_requires_configuration(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(ValueError, "OFFICE_MODEL"):
                await run_office_agent(sheet(), "explain", "model")

    async def test_model_response_is_bound_to_original_target(self):
        mock_response = type("Response", (), {"is_error": False, "json": lambda self: {
            "choices": [{"message": {"content": json.dumps({"answer": "Done", "replacement": [["new", 0, "=B1+1"]]})}}]}})()
        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.post.return_value = mock_response
        with patch.dict(os.environ, {"OFFICE_MODEL_BASE_URL": "https://example.test/v1", "OFFICE_MODEL_NAME": "test"}), patch("office_model_repository.httpx.AsyncClient", return_value=mock_client):
            result = await run_office_agent(sheet(), "rewrite", "model")
        self.assertEqual(result["patch"]["selection"], sheet()["selection"])
        sent = mock_client.post.call_args.kwargs["json"]
        self.assertIn("spreadsheet", sent["messages"][0]["content"])

    def test_multiblock_document_rewrite_allowed(self):
        context = OfficeContext.model_validate({"document_id": "doc", "surface": "document", "revision": 0,
            "selection": {"from": 1, "to": 8, "text": "abc\ndef", "single_block": False}})
        patch = create_patch(context, "新的段落")
        self.assertEqual(patch["replacement"], "新的段落")

    def test_research_diagram_context_and_patch(self):
        source = '{"format":"invp-research-diagram-v1","type":"timeline","title":"事件","events":[]}'
        context = OfficeContext.model_validate({
            "document_id": "research-1",
            "surface": "research",
            "revision": 2,
            "selection": {"text": source, "diagram_type": "timeline"},
            "surrounding": source,
        })
        replacement = source.replace('"事件"', '"关键事件"')
        patch = create_patch(context, replacement)
        self.assertEqual(patch["replacement"], replacement)
        self.assertTrue(validate_office_patch(patch, context.model_dump())["valid"])


if __name__ == "__main__":
    unittest.main()
