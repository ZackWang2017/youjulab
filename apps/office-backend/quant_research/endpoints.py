from __future__ import annotations

import asyncio
import json
import tempfile
import threading
from pathlib import Path
from uuid import uuid4
from fastapi import Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from config import sqlite_path
from model_runtime import model_configured, model_configuration_error
from .agent import research_events
from .schema import parse_prices, strategy_content, validate_plan
from .repository import RunRepository, public_run
from .service import ResearchService
from .sandbox import available


class ResearchBody(BaseModel):
    instruction: str = Field(min_length=1, max_length=12000)
    revision: int = Field(ge=0)


class DataBody(BaseModel):
    csv: str = Field(max_length=4_000_000)
    name: str = Field(default="prices.csv", max_length=100)
    basis: str
    source: str = Field(min_length=1, max_length=500)
    revision: int = Field(ge=0)


class RunBody(BaseModel):
    revision: int = Field(ge=0)
    request_key: str = Field(min_length=1, max_length=100)


def register_routes(app, user_from, get_store, asset_of, public_asset):
    repository = None
    service = None
    locks: dict[str, asyncio.Lock] = {}
    resource_lock = threading.Lock()

    def resources():
        nonlocal repository, service
        with resource_lock:
            if repository is None:
                test_store = app.state.store.__class__.__name__ == "MemoryStore"
                # Keep runs in the existing office database so normal office backups include them.
                path = Path(tempfile.mkdtemp(prefix="quant-tests-")) / "runs.sqlite" if test_store else Path(getattr(app.state.store, "path", sqlite_path()))
                repository = RunRepository(path)
                service = ResearchService(repository)
        return repository, service

    def asset_for(store, user, asset_id):
        asset = asset_of(store, user, asset_id)
        if not asset or asset["kind"] != "strategy":
            raise HTTPException(404, "策略资产不存在。")
        return asset

    def checked(asset, revision):
        if asset["revision"] != revision:
            raise HTTPException(409, "策略已在其他会话更新，请重新打开。")

    @app.get("/quant/capabilities")
    def capabilities(user=Depends(user_from)):
        import importlib.util
        return {"tools": [{"id": "bt_allocation", "name": "资产配置与再平衡", "ready": importlib.util.find_spec("bt") is not None}, {"id": "python_research", "name": "自定义 Python 研究", **available()}], "data_sources": ["csv"], "financial_pit": False}

    @app.put("/assets/{asset_id}/strategy/data")
    async def import_data(asset_id: str, body: DataBody, user=Depends(user_from), store=Depends(get_store)):
        async with locks.setdefault(asset_id, asyncio.Lock()):
            asset = asset_for(store, user, asset_id)
            checked(asset, body.revision)
            try:
                content = strategy_content(asset["content"])
                content["dataset"] = {**parse_prices(body.csv, body.name), "basis": body.basis, "source": body.source}
                content["plan"] = None
                content = strategy_content(content)
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from exc
            saved = store.update_asset(asset["owner_id"], asset_id, content=content, revision=asset["revision"] + 1)
            return public_asset(saved, include_content=True)

    @app.post("/assets/{asset_id}/strategy/research")
    async def research(asset_id: str, body: ResearchBody, user=Depends(user_from), store=Depends(get_store)):
        initial = asset_for(store, user, asset_id)
        checked(initial, body.revision)
        if not model_configured():
            raise HTTPException(503, model_configuration_error())
        async def stream():
            lock = locks.setdefault(asset_id, asyncio.Lock())
            if lock.locked():
                yield 'data: ' + json.dumps({"type": "error", "error": "当前策略正在生成研究计划。"}, ensure_ascii=False) + '\n\n'
                return
            async with lock:
                try:
                    current = asset_for(store, user, asset_id)
                    checked(current, body.revision)
                    content = strategy_content(current["content"])
                    async for event in research_events(content, body.instruction):
                        if event["type"] == "done":
                            latest = asset_for(store, user, asset_id)
                            checked(latest, body.revision)
                            plan = event["plan"]
                            content.update(objective=body.instruction, plan=plan, code=plan["code"], messages=[*content["messages"], {"role": "user", "content": body.instruction}, {"role": "assistant", "content": plan["summary"]}][-30:])
                            saved = store.update_asset(current["owner_id"], asset_id, content=strategy_content(content), revision=current["revision"] + 1)
                            event["asset"] = public_asset(saved, include_content=True)
                        yield 'data: ' + json.dumps(event, ensure_ascii=False) + '\n\n'
                except Exception as exc:
                    message = exc.detail if isinstance(exc, HTTPException) else str(exc)
                    yield 'data: ' + json.dumps({"type": "error", "error": message}, ensure_ascii=False) + '\n\n'
        return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.post("/assets/{asset_id}/strategy/runs")
    def create_run(asset_id: str, body: RunBody, user=Depends(user_from), store=Depends(get_store)):
        asset = asset_for(store, user, asset_id)
        checked(asset, body.revision)
        try:
            content = strategy_content(asset["content"])
            if not content["dataset"] or not content["plan"] or content["plan"]["tool"] == "needs_input":
                raise ValueError("请先导入真实数据并生成可执行研究计划。")
            validate_plan({**content["plan"], "code": content["code"]}, content["dataset"]["symbols"])
            repo, worker = resources()
            run, created = repo.create(user["id"], asset_id, body.request_key, {"revision": asset["revision"], "content": content})
            if created:
                worker.start(run)
            return public_run(run)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.get("/assets/{asset_id}/strategy/runs")
    def list_runs(asset_id: str, user=Depends(user_from), store=Depends(get_store)):
        asset_for(store, user, asset_id)
        repo, _ = resources()
        return {"runs": [public_run(run) for run in repo.list(user["id"], asset_id)]}

    def run_for(asset_id, run_id, user, store):
        asset_for(store, user, asset_id)
        repo, _ = resources()
        run = repo.get(run_id)
        if not run or run["owner_id"] != user["id"] or run["asset_id"] != asset_id:
            raise HTTPException(404, "研究任务不存在。")
        return repo, run

    @app.get("/assets/{asset_id}/strategy/runs/{run_id}")
    def get_run(asset_id: str, run_id: str, user=Depends(user_from), store=Depends(get_store)):
        _, run = run_for(asset_id, run_id, user, store)
        return public_run(run, detail=True)

    @app.post("/assets/{asset_id}/strategy/runs/{run_id}/cancel")
    def cancel_run(asset_id: str, run_id: str, user=Depends(user_from), store=Depends(get_store)):
        repo, run = run_for(asset_id, run_id, user, store)
        if run["status"] in {"queued", "running", "validating"}:
            run = repo.update(run_id, status="cancelled", message="用户取消了任务。")
        return public_run(run, detail=True)
