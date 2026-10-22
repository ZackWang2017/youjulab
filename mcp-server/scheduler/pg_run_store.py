"""计划任务运行记录落数据库，重启后不重复跑。"""
from __future__ import annotations

import threading
from datetime import date

from db.connection import get_connection

from .daily_scheduler import RunRecord

_DDL = """
CREATE TABLE IF NOT EXISTS scheduled_task_runs (
    task_name   VARCHAR(80) NOT NULL,
    run_date    DATE NOT NULL,
    status      VARCHAR(12) NOT NULL,
    started_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at TIMESTAMPTZ,
    detail      TEXT,
    PRIMARY KEY (task_name, run_date)
);
"""


class PgRunStore:
    def __init__(self) -> None:
        self._ready = False
        self._lock = threading.Lock()

    def _ensure(self) -> None:
        if self._ready:
            return
        with self._lock:
            if self._ready:
                return
            with get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(_DDL)
            self._ready = True

    def get(self, task_name: str, run_date: date) -> RunRecord | None:
        self._ensure()
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT * FROM scheduled_task_runs WHERE task_name = %s AND run_date = %s",
                    (task_name, run_date),
                )
                row = cur.fetchone()
        return _record(row) if row else None

    def mark(self, task_name: str, run_date: date, status: str, detail: str | None = None) -> None:
        self._ensure()
        finished = status != "running"
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO scheduled_task_runs (task_name, run_date, status, detail, finished_at)
                    VALUES (%s, %s, %s, %s, CASE WHEN %s THEN NOW() ELSE NULL END)
                    ON CONFLICT (task_name, run_date) DO UPDATE SET
                        status = EXCLUDED.status,
                        detail = EXCLUDED.detail,
                        started_at = CASE WHEN EXCLUDED.status = 'running' THEN NOW() ELSE scheduled_task_runs.started_at END,
                        finished_at = EXCLUDED.finished_at
                    """,
                    (task_name, run_date, status, detail, finished),
                )

    def recent(self, limit: int = 40) -> list[RunRecord]:
        self._ensure()
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT * FROM scheduled_task_runs ORDER BY run_date DESC, started_at DESC LIMIT %s",
                    (limit,),
                )
                return [_record(r) for r in cur.fetchall()]


def _record(row: dict) -> RunRecord:
    data = dict(row)
    return RunRecord(
        task_name=data["task_name"],
        run_date=data["run_date"],
        status=data["status"],
        started_at=data.get("started_at"),
        finished_at=data.get("finished_at"),
        detail=data.get("detail"),
    )
