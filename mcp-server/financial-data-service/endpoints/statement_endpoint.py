"""财务数据 REST API。"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any, Callable

from starlette.requests import Request
from starlette.responses import JSONResponse

HERE = Path(__file__).resolve().parents[2]
SERVICE_PATH = HERE / "financial-data-service" / "service" / "statement_service.py"


def _load_statement_service():
    spec = importlib.util.spec_from_file_location("financial_statement_service", SERVICE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load service module: {SERVICE_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_statement_service = _load_statement_service()
query_financial_statements = _statement_service.query_financial_statements
SUPPORTED_CURRENCY_UNITS = _statement_service.SUPPORTED_CURRENCY_UNITS
DEFAULT_CURRENCY_UNIT = _statement_service.DEFAULT_CURRENCY_UNIT


def _parse_optional_int(raw: str | None) -> int | None:
    if raw is None or raw.strip() == "":
        return None
    return int(raw)


def _query_params_from_request(request: Request) -> dict[str, Any]:
    params = request.query_params
    return {
        "ticker_code": params.get("ticker_code") or None,
        "market_code": params.get("market_code") or None,
        "fiscal_year": _parse_optional_int(params.get("fiscal_year")),
        "report_period": params.get("report_period") or None,
        "item_category": params.get("item_category") or None,
        "source_item_name": params.get("source_item_name") or None,
        "standard_item_name": params.get("standard_item_name") or None,
        "currency_unit": params.get("currency_unit") or DEFAULT_CURRENCY_UNIT,
    }


def _ok(data: Any, *, headers: dict[str, str]) -> JSONResponse:
    return JSONResponse({"ok": True, "data": data}, headers=headers)


def _error(message: str, *, status: int, headers: dict[str, str]) -> JSONResponse:
    return JSONResponse({"ok": False, "error": message}, status_code=status, headers=headers)


async def list_financial_statements(request: Request, headers: dict[str, str]) -> JSONResponse:
    try:
        payload = _query_params_from_request(request)
        rows = query_financial_statements(**payload)
    except ValueError as exc:
        return _error(str(exc), status=400, headers=headers)
    except Exception as exc:  # noqa: BLE001
        return _error(str(exc), status=500, headers=headers)

    return _ok(
        {
            "total": len(rows),
            "currency_unit": payload["currency_unit"],
            "items": rows,
        },
        headers=headers,
    )


async def list_financial_item_names(request: Request, headers: dict[str, str]) -> JSONResponse:
    """返回可选的财报科目列表，供前端多选折线。"""
    try:
        payload = _query_params_from_request(request)
        payload.pop("source_item_name", None)
        payload.pop("standard_item_name", None)
        rows = query_financial_statements(**payload)
        names = sorted({row["source_item_name"] for row in rows if row.get("source_item_name")})
    except ValueError as exc:
        return _error(str(exc), status=400, headers=headers)
    except Exception as exc:  # noqa: BLE001
        return _error(str(exc), status=500, headers=headers)

    return _ok({"total": len(names), "items": names}, headers=headers)


def register_routes(
    app: Any,
    *,
    cors_headers: dict[str, str],
) -> None:
    async def statements_route(request: Request):
        return await list_financial_statements(request, cors_headers)

    async def item_names_route(request: Request):
        return await list_financial_item_names(request, cors_headers)

    async def meta_route(_request: Request):
        return _ok(
            {
                "currency_units": list(SUPPORTED_CURRENCY_UNITS),
                "default_currency_unit": DEFAULT_CURRENCY_UNIT,
                "query_fields": [
                    "ticker_code",
                    "market_code",
                    "fiscal_year",
                    "report_period",
                    "item_category",
                    "source_item_name",
                    "standard_item_name",
                    "currency_unit",
                ],
            },
            headers=cors_headers,
        )

    app.add_route("/api/financial/statements", statements_route, methods=["GET", "OPTIONS"])
    app.add_route("/api/financial/item-names", item_names_route, methods=["GET", "OPTIONS"])
    app.add_route("/api/financial/meta", meta_route, methods=["GET", "OPTIONS"])
