"""每日定时任务调度器。

设计目标：
  - 任务声明「每天几点跑」（本地时区），默认只在工作日触发；
  - 同一天同一任务只成功一次，运行记录落库（重启不重复跑）；
  - 服务在触发时间之后才启动时自动补跑当天任务；
  - 任务可声明依赖（例如先同步收盘价再算净值），依赖当天成功后才会执行；
  - 失败后间隔一段时间自动重试，也可以通过 REST 手动触发；
  - 任务函数是同步阻塞代码（psycopg / httpx），在线程里跑，不卡事件循环。
"""
from __future__ import annotations

import asyncio
import logging
import threading
import traceback
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Callable, Protocol
from zoneinfo import ZoneInfo

log = logging.getLogger("scheduler")

TaskFn = Callable[[date], dict | None]


@dataclass(slots=True)
class RunRecord:
    task_name: str
    run_date: date
    status: str  # running | success | failed
    started_at: datetime | None = None
    finished_at: datetime | None = None
    detail: str | None = None


class RunStore(Protocol):
    def get(self, task_name: str, run_date: date) -> RunRecord | None: ...

    def mark(self, task_name: str, run_date: date, status: str, detail: str | None = None) -> None: ...

    def recent(self, limit: int = 40) -> list[RunRecord]: ...


class MemoryRunStore:
    """进程内记录，测试用。"""

    def __init__(self) -> None:
        self._rows: dict[tuple[str, date], RunRecord] = {}

    def get(self, task_name: str, run_date: date) -> RunRecord | None:
        return self._rows.get((task_name, run_date))

    def mark(self, task_name: str, run_date: date, status: str, detail: str | None = None) -> None:
        now = datetime.now()
        row = self._rows.get((task_name, run_date))
        if row is None or status == "running":
            row = RunRecord(task_name, run_date, status, started_at=now, detail=detail)
        else:
            row.status = status
            row.detail = detail
        if status != "running":
            row.finished_at = now
        self._rows[(task_name, run_date)] = row

    def recent(self, limit: int = 40) -> list[RunRecord]:
        rows = sorted(self._rows.values(), key=lambda r: (r.run_date, r.started_at or datetime.min), reverse=True)
        return rows[:limit]


@dataclass(slots=True)
class DailyTask:
    name: str
    run: TaskFn
    at: time = time(17, 0)
    weekdays_only: bool = True
    description: str = ""
    depends_on: tuple[str, ...] = ()
    retry_after: timedelta = timedelta(minutes=30)
    max_attempts: int = 3
    _attempts: dict[date, int] = field(default_factory=dict, repr=False)


class DailyTaskScheduler:
    def __init__(self, store: RunStore, *, tz: str = "Asia/Shanghai", poll_seconds: int = 30, enabled: bool = True):
        self._store = store
        self._tz = ZoneInfo(tz)
        self._poll = max(5, poll_seconds)
        self._enabled = enabled
        self._tasks: dict[str, DailyTask] = {}
        self._lock = threading.Lock()
        self._running: set[str] = set()

    # ---- 注册与查询 ------------------------------------------------------

    def register(self, task: DailyTask) -> DailyTask:
        for dep in task.depends_on:
            if dep not in self._tasks:
                raise ValueError(f"task {task.name} depends on unknown task {dep}")
        self._tasks[task.name] = task
        return task

    def task(self, name: str) -> DailyTask | None:
        return self._tasks.get(name)

    @property
    def tz(self) -> ZoneInfo:
        return self._tz

    def now(self) -> datetime:
        return datetime.now(self._tz)

    def describe(self) -> list[dict]:
        today = self.now().date()
        out = []
        for task in self._tasks.values():
            record = self._store.get(task.name, today)
            out.append(
                {
                    "name": task.name,
                    "description": task.description,
                    "at": task.at.strftime("%H:%M"),
                    "weekdays_only": task.weekdays_only,
                    "depends_on": list(task.depends_on),
                    "timezone": str(self._tz),
                    "today": today.isoformat(),
                    "today_status": record.status if record else None,
                    "today_detail": record.detail if record else None,
                    "today_finished_at": record.finished_at.isoformat() if record and record.finished_at else None,
                    "running": task.name in self._running,
                }
            )
        return out

    def history(self, limit: int = 40) -> list[dict]:
        return [
            {
                "name": r.task_name,
                "run_date": r.run_date.isoformat(),
                "status": r.status,
                "started_at": r.started_at.isoformat() if r.started_at else None,
                "finished_at": r.finished_at.isoformat() if r.finished_at else None,
                "detail": r.detail,
            }
            for r in self._store.recent(limit)
        ]

    # ---- 触发判断 --------------------------------------------------------

    def is_due(self, task: DailyTask, now: datetime | None = None) -> bool:
        now = now or self.now()
        if task.weekdays_only and now.weekday() >= 5:
            return False
        if now.time() < task.at:
            return False
        today = now.date()
        if task.name in self._running:
            return False
        for dep in task.depends_on:
            dep_record = self._store.get(dep, today)
            if not dep_record or dep_record.status != "success":
                return False
        record = self._store.get(task.name, today)
        if record is None:
            return True
        if record.status == "success":
            return False
        if record.status == "running":
            # 上次进程异常退出留下的 running 记录：超过 2 小时视为失败可重试
            started = record.started_at or now
            if started.tzinfo is None:
                started = started.replace(tzinfo=self._tz)
            return now - started > timedelta(hours=2)
        attempts = task._attempts.get(today, 1)
        if attempts >= task.max_attempts:
            return False
        finished = record.finished_at or now
        if finished.tzinfo is None:
            finished = finished.replace(tzinfo=self._tz)
        return now - finished >= task.retry_after

    # ---- 执行 ------------------------------------------------------------

    def run_task(self, name: str, *, run_date: date | None = None, force: bool = False) -> dict:
        """同步执行一个任务（阻塞），REST 手动触发和轮询都用它。"""
        task = self._tasks.get(name)
        if not task:
            raise KeyError(name)
        today = run_date or self.now().date()
        with self._lock:
            if name in self._running:
                return {"name": name, "run_date": today.isoformat(), "status": "running", "detail": "任务正在执行。"}
            if not force:
                record = self._store.get(name, today)
                if record and record.status == "success":
                    return {"name": name, "run_date": today.isoformat(), "status": "success", "detail": record.detail, "skipped": True}
            self._running.add(name)
        task._attempts[today] = task._attempts.get(today, 0) + 1
        self._store.mark(name, today, "running")
        log.info("scheduler: %s start run_date=%s", name, today)
        try:
            result = task.run(today) or {}
            detail = _short(result)
            self._store.mark(name, today, "success", detail)
            log.info("scheduler: %s ok %s", name, detail)
            return {"name": name, "run_date": today.isoformat(), "status": "success", "detail": detail, "result": result}
        except Exception as exc:  # noqa: BLE001
            detail = f"{type(exc).__name__}: {exc}"[:800]
            log.warning("scheduler: %s failed: %s\n%s", name, detail, traceback.format_exc())
            self._store.mark(name, today, "failed", detail)
            return {"name": name, "run_date": today.isoformat(), "status": "failed", "detail": detail}
        finally:
            with self._lock:
                self._running.discard(name)

    def tick(self, now: datetime | None = None) -> list[dict]:
        """跑一轮到期任务（按注册顺序，依赖链在同一轮内可依次满足）。"""
        results = []
        for task in list(self._tasks.values()):
            if self.is_due(task, now):
                results.append(self.run_task(task.name, run_date=(now or self.now()).date()))
        return results

    async def loop(self, stop: asyncio.Event) -> None:
        if not self._enabled:
            log.info("scheduler disabled")
            return
        log.info("scheduler started tz=%s tasks=%s", self._tz, list(self._tasks))
        while not stop.is_set():
            try:
                await asyncio.to_thread(self.tick)
            except Exception as exc:  # noqa: BLE001
                log.warning("scheduler tick error: %s", exc)
            try:
                await asyncio.wait_for(stop.wait(), timeout=self._poll)
            except asyncio.TimeoutError:
                continue


def _short(result: dict) -> str:
    try:
        import json

        return json.dumps(result, ensure_ascii=False, default=str)[:800]
    except Exception:  # noqa: BLE001
        return str(result)[:800]
