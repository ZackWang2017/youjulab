"""每日资讯调度：进程内每分钟扫描一次到期任务。"""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from digest_job import due_jobs, run_digest_job

if TYPE_CHECKING:
    from store import Store

log = logging.getLogger("digest_scheduler")


async def digest_scheduler_loop(store: "Store", stop: asyncio.Event) -> None:
    while not stop.is_set():
        try:
            jobs = due_jobs(store)
            for job in jobs:
                try:
                    await run_digest_job(store, job)
                    log.info("digest ok document=%s", job.get("document_id"))
                except Exception as exc:
                    log.warning("digest fail document=%s err=%s", job.get("document_id"), exc)
                    try:
                        store.upsert_digest_job(
                            job["user_id"],
                            job["document_id"],
                            {
                                **job,
                                "last_status": "failed",
                                "last_error": str(exc)[:800],
                            },
                        )
                    except Exception:
                        pass
        except Exception as exc:
            log.warning("digest scan error: %s", exc)
        try:
            await asyncio.wait_for(stop.wait(), timeout=60)
        except asyncio.TimeoutError:
            continue
