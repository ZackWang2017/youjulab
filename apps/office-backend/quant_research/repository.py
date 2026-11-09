from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import threading
from datetime import datetime, timezone
from uuid import uuid4


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


class RunRepository:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(path, timeout=30, check_same_thread=False)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("CREATE TABLE IF NOT EXISTS quant_runs (id TEXT PRIMARY KEY, owner TEXT, asset TEXT, request_key TEXT, data TEXT, UNIQUE(owner,asset,request_key))")
        self.db.commit()
        # A worker cannot survive process restart; retain the snapshot and expose an honest terminal state.
        for (raw,) in self.db.execute("SELECT data FROM quant_runs").fetchall():
            run = json.loads(raw)
            if run["status"] in {"queued", "running", "validating"}:
                self.update(run["id"], status="failed", error="后台服务重启，任务已中断。可基于原版本重新运行。")

    def create(self, owner: str, asset: str, key: str, snapshot: dict) -> tuple[dict, bool]:
        with self.lock:
            row = self.db.execute("SELECT data FROM quant_runs WHERE owner=? AND asset=? AND request_key=?", (owner, asset, key)).fetchone()
            if row:
                return json.loads(row[0]), False
            active = self.db.execute("SELECT 1 FROM quant_runs WHERE owner=? AND asset=? AND json_extract(data,'$.status') IN ('queued','running','validating') LIMIT 1", (owner, asset)).fetchone()
            if active:
                raise ValueError("该策略已有运行中的任务。")
            run = {"id": uuid4().hex, "owner_id": owner, "asset_id": asset, "created_at": now(), "updated_at": now(), "status": "queued", "snapshot": snapshot, "events": [{"time": now(), "message": "任务已创建，研究版本与数据快照已冻结。"}], "result": None, "error": ""}
            self.db.execute("INSERT INTO quant_runs VALUES (?,?,?,?,?)", (run["id"], owner, asset, key, json.dumps(run, ensure_ascii=False)))
            self.db.commit()
            return run, True

    def get(self, run_id: str) -> dict | None:
        with self.lock:
            row = self.db.execute("SELECT data FROM quant_runs WHERE id=?", (run_id,)).fetchone()
            return json.loads(row[0]) if row else None

    def list(self, owner: str, asset: str) -> list[dict]:
        with self.lock:
            rows = self.db.execute("SELECT json_remove(data,'$.snapshot.content.dataset','$.snapshot.content.messages','$.result') FROM quant_runs WHERE owner=? AND asset=? ORDER BY rowid DESC LIMIT 30", (owner, asset)).fetchall()
            return [json.loads(row[0]) for row in rows]

    def update(self, run_id: str, message: str = "", **fields) -> dict:
        with self.lock:
            run = self.get(run_id)
            if run is None:
                raise ValueError("任务不存在。")
            if run["status"] == "cancelled":
                return run
            run.update(fields, updated_at=now())
            if message:
                run["events"].append({"time": now(), "message": message})
            self.db.execute("UPDATE quant_runs SET data=? WHERE id=?", (json.dumps(run, ensure_ascii=False, allow_nan=False), run_id))
            self.db.commit()
            return run


def public_run(run: dict, *, detail: bool = False) -> dict:
    result = {k: v for k, v in run.items() if k not in {"owner_id", "snapshot", "result"}}
    snapshot = run["snapshot"]
    result["revision"] = snapshot["revision"]
    result["tool"] = snapshot["content"]["plan"]["tool"]
    if detail:
        content = snapshot["content"]
        result.update(result=run["result"], code=content["code"], plan=content["plan"], objective=content["objective"], data_hash=content["dataset"]["sha256"])
    return result

