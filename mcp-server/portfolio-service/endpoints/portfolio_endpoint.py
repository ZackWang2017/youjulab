"""持仓管理 REST：/api/portfolio/*。业务都在本服务，office-backend 只转发并带上用户 id。

鉴权沿用静态 MCP_ACCESS_TOKEN（server.py 的 guard），用户由 ``X-Office-User-Id`` 头标识。
同时注册每日计划任务：工作日 17:00 快照汇率 → 补收盘价 → 重算净值，并暴露 /api/scheduler/*。
"""
from __future__ import annotations

import asyncio
import json
import os
from datetime import time as time_t

from starlette.requests import Request
from starlette.responses import JSONResponse

import market_sync_service as sync
import portfolio_service as service
from portfolio_service import PortfolioError
from scheduler import DailyTask, DailyTaskScheduler

_scheduler: DailyTaskScheduler | None = None


def _owner(request: Request) -> str:
    owner = (request.headers.get("x-office-user-id") or "").strip()
    if not owner:
        raise PortfolioError("缺少用户标识。", 401)
    return owner


async def _body(request: Request) -> dict:
    raw = await request.body()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except ValueError:
        raise PortfolioError("请求体不是合法 JSON。") from None
    if not isinstance(data, dict):
        raise PortfolioError("请求体必须是对象。")
    return data


def _int(value: str, label: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        raise PortfolioError(f"{label} 无效。") from None


def _handler(fn):
    async def route(request: Request):
        cors = request.app.state.portfolio_cors
        try:
            payload = await fn(request)
        except PortfolioError as exc:
            return JSONResponse({"error": str(exc)}, status_code=exc.status, headers=cors)
        except KeyError as exc:
            return JSONResponse({"error": f"未知任务：{exc}"}, status_code=404, headers=cors)
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": f"持仓服务错误：{exc}"}, status_code=500, headers=cors)
        return JSONResponse(payload, headers=cors)

    return route


def _parse_at(raw: str) -> time_t:
    try:
        hour, minute = raw.strip().split(":")
        return time_t(int(hour), int(minute))
    except (ValueError, AttributeError):
        return time_t(17, 0)


def build_scheduler() -> DailyTaskScheduler:
    """注册收盘后任务链。时区/时间/开关都可通过环境变量覆盖。"""
    from scheduler.pg_run_store import PgRunStore

    tz = (os.environ.get("SCHEDULER_TZ") or os.environ.get("DIGEST_TZ") or "Asia/Shanghai").strip()
    at = _parse_at(os.environ.get("PORTFOLIO_CLOSE_JOB_AT") or "17:00")
    enabled = (os.environ.get("SCHEDULER_ENABLED") or "1").strip().lower() not in {"0", "false", "no"}
    scheduler = DailyTaskScheduler(PgRunStore(), tz=tz, enabled=enabled, poll_seconds=60)
    scheduler.register(DailyTask(
        name="market.snapshot_fx",
        run=sync.snapshot_fx_rates,
        at=at,
        description="快照当日 USD/HKD 兑人民币牌价",
    ))
    scheduler.register(DailyTask(
        name="market.sync_close_prices",
        run=sync.sync_close_prices,
        at=at,
        description="补齐所有组合持仓证券的日收盘价",
    ))
    scheduler.register(DailyTask(
        name="portfolio.recompute_nav",
        run=sync.recompute_all_nav,
        at=at,
        depends_on=("market.snapshot_fx", "market.sync_close_prices"),
        description="按净值法重算所有组合的每日净值与 XIRR",
    ))
    return scheduler


def get_scheduler() -> DailyTaskScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = build_scheduler()
    return _scheduler


def register_routes(app, cors_headers: dict[str, str]) -> None:
    app.state.portfolio_cors = cors_headers
    scheduler = get_scheduler()

    # ---- 组合 ---------------------------------------------------------------
    @_handler
    async def portfolios(request: Request):
        owner = _owner(request)
        if request.method == "POST":
            body = await _body(request)
            return service.create_portfolio(owner, str(body.get("name") or ""))
        return {"portfolios": service.list_portfolios(owner)}

    @_handler
    async def portfolio_item(request: Request):
        owner = _owner(request)
        pid = _int(request.path_params["pid"], "组合 id")
        if request.method == "DELETE":
            service.delete_portfolio(owner, pid)
            return {"ok": True}
        body = await _body(request)
        return service.rename_portfolio(owner, pid, str(body.get("name") or ""))

    # ---- 持仓 ---------------------------------------------------------------
    @_handler
    async def holdings(request: Request):
        owner = _owner(request)
        pid = _int(request.path_params["pid"], "组合 id")
        if request.method == "POST":
            body = await _body(request)
            return await asyncio.to_thread(service.buy, owner, pid, body)
        refresh = (request.query_params.get("refresh") or "1") != "0"
        return await asyncio.to_thread(service.holdings_overview, owner, pid, refresh_live=refresh)

    @_handler
    async def holding_transactions(request: Request):
        owner = _owner(request)
        pid = _int(request.path_params["pid"], "组合 id")
        hid = _int(request.path_params["hid"], "持仓 id")
        if request.method == "POST":
            body = await _body(request)
            return await asyncio.to_thread(service.transact, owner, pid, hid, body)
        return {"transactions": service.list_transactions(owner, pid, hid)}

    @_handler
    async def transactions(request: Request):
        owner = _owner(request)
        pid = _int(request.path_params["pid"], "组合 id")
        return {"transactions": service.list_transactions(owner, pid)}

    # ---- 净值 / 收益 ---------------------------------------------------------
    @_handler
    async def stats(request: Request):
        owner = _owner(request)
        pid = _int(request.path_params["pid"], "组合 id")
        refresh = (request.query_params.get("refresh") or "0") == "1"
        return await asyncio.to_thread(service.stats, owner, pid, refresh=refresh)

    @_handler
    async def stats_refresh(request: Request):
        """手动「立即更新」：拉汇率 + 补收盘价 + 重算本组合净值。"""
        owner = _owner(request)
        pid = _int(request.path_params["pid"], "组合 id")

        def run():
            sync.snapshot_fx_rates()
            sync.sync_close_prices()
            service.recompute_nav(pid)
            return service.stats(owner, pid)

        return await asyncio.to_thread(run)

    # ---- 全部持仓统计（跨组合聚合，只读） -----------------------------------------
    @_handler
    async def overview_holdings(request: Request):
        owner = _owner(request)
        refresh = (request.query_params.get("refresh") or "1") != "0"
        return await asyncio.to_thread(service.all_holdings_overview, owner, refresh_live=refresh)

    @_handler
    async def overview_stats(request: Request):
        owner = _owner(request)
        return await asyncio.to_thread(service.all_stats, owner)

    @_handler
    async def overview_stats_refresh(request: Request):
        """手动「立即更新」：拉汇率 + 补收盘价后即时重算全部持仓的净值。"""
        owner = _owner(request)

        def run():
            sync.snapshot_fx_rates()
            sync.sync_close_prices()
            return service.all_stats(owner)

        return await asyncio.to_thread(run)

    # ---- 计划任务 -----------------------------------------------------------
    @_handler
    async def scheduler_tasks(request: Request):
        return {"tasks": scheduler.describe(), "history": scheduler.history(30)}

    @_handler
    async def scheduler_run(request: Request):
        name = request.path_params["name"]
        force = (request.query_params.get("force") or "1") != "0"
        return await asyncio.to_thread(scheduler.run_task, name, force=force)

    app.add_route("/api/portfolio/portfolios", portfolios, methods=["GET", "POST", "OPTIONS"])
    app.add_route("/api/portfolio/portfolios/{pid}", portfolio_item, methods=["PUT", "DELETE", "OPTIONS"])
    app.add_route("/api/portfolio/portfolios/{pid}/holdings", holdings, methods=["GET", "POST", "OPTIONS"])
    app.add_route("/api/portfolio/portfolios/{pid}/holdings/{hid}/transactions", holding_transactions, methods=["GET", "POST", "OPTIONS"])
    app.add_route("/api/portfolio/portfolios/{pid}/transactions", transactions, methods=["GET", "OPTIONS"])
    app.add_route("/api/portfolio/portfolios/{pid}/stats", stats, methods=["GET", "OPTIONS"])
    app.add_route("/api/portfolio/portfolios/{pid}/stats/refresh", stats_refresh, methods=["POST", "OPTIONS"])
    app.add_route("/api/portfolio/overview/holdings", overview_holdings, methods=["GET", "OPTIONS"])
    app.add_route("/api/portfolio/overview/stats", overview_stats, methods=["GET", "OPTIONS"])
    app.add_route("/api/portfolio/overview/stats/refresh", overview_stats_refresh, methods=["POST", "OPTIONS"])
    app.add_route("/api/scheduler/tasks", scheduler_tasks, methods=["GET", "OPTIONS"])
    app.add_route("/api/scheduler/tasks/{name}/run", scheduler_run, methods=["POST", "OPTIONS"])


def mount_scheduler(app) -> None:
    """把调度循环挂进 Starlette 生命周期，与 MCP session manager 共存。"""
    from contextlib import asynccontextmanager

    scheduler = get_scheduler()
    inner = app.router.lifespan_context

    @asynccontextmanager
    async def combined(scope_app):
        async with inner(scope_app):
            stop = asyncio.Event()
            task = asyncio.create_task(scheduler.loop(stop))
            try:
                yield
            finally:
                stop.set()
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):  # noqa: BLE001
                    pass

    app.router.lifespan_context = combined
