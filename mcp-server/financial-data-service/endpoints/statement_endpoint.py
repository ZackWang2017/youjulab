"""财务数据 REST API。"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any, Callable

from starlette.requests import Request
from starlette.responses import JSONResponse

HERE = Path(__file__).resolve().parents[2]
SERVICE_DIR = HERE / "financial-data-service" / "service"
SERVICE_PATH = SERVICE_DIR / "statement_service.py"
ENSURE_PATH = SERVICE_DIR / "statement_ensure.py"


def _load_module(name: str, path: Path):
    loaded = sys.modules.get(name)
    if loaded is not None:
        return loaded
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load service module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_statement_service = _load_module("financial_statement_service", SERVICE_PATH)
_statement_ensure = _load_module("financial_statement_ensure", ENSURE_PATH)
query_statements_with_sync = _statement_ensure.query_statements_with_sync
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
        result = query_statements_with_sync(**payload)
    except ValueError as exc:
        return _error(str(exc), status=400, headers=headers)
    except Exception as exc:  # noqa: BLE001
        return _error(str(exc), status=500, headers=headers)

    return _ok(
        {
            "total": len(result["items"]),
            "currency_unit": result["currency_unit"],
            "items": result["items"],
            "sync": result["sync"],
        },
        headers=headers,
    )


async def list_financial_item_names(request: Request, headers: dict[str, str]) -> JSONResponse:
    """返回可选的财报科目列表，供前端多选折线。"""
    try:
        payload = _query_params_from_request(request)
        payload.pop("source_item_name", None)
        payload.pop("standard_item_name", None)
        result = query_statements_with_sync(**payload)
        names = sorted({row["source_item_name"] for row in result["items"] if row.get("source_item_name")})
    except ValueError as exc:
        return _error(str(exc), status=400, headers=headers)
    except Exception as exc:  # noqa: BLE001
        return _error(str(exc), status=500, headers=headers)

    return _ok({"total": len(names), "items": names, "sync": result["sync"]}, headers=headers)


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
