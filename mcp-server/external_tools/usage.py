"""百度 / 百度百科 / Google 搜索：开关 + 每日次数配额（PostgreSQL）。"""
from __future__ import annotations

import os
import threading
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

_lock = threading.Lock()
_ensured = False

ENGINE_BAIDU = "baidu"
ENGINE_BAIKE = "baike"
ENGINE_GOOGLE = "google"


def _tz() -> ZoneInfo:
    name = (os.environ.get("SEARCH_USAGE_TZ") or os.environ.get("DIGEST_TZ") or "Asia/Shanghai").strip()
    try:
        return ZoneInfo(name)
    except Exception:
        return ZoneInfo("Asia/Shanghai")


def today_str() -> str:
    return datetime.now(_tz()).date().isoformat()


def _pg_url() -> str:
    return (os.environ.get("OFFICE_PG_URL") or "").strip()


def _env_bool(key: str, default: bool = True) -> bool:
    raw = (os.environ.get(key) or "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


def _env_int(key: str, default: int) -> int:
    raw = (os.environ.get(key) or "").strip()
    if not raw:
        return default
    try:
        return max(0, int(raw))
    except ValueError:
        return default


def engine_enabled(engine: str) -> bool:
    if engine == ENGINE_BAIDU:
        return _env_bool("SEARCH_BAIDU_ENABLED", True)
    if engine == ENGINE_BAIKE:
        return _env_bool("SEARCH_BAIKE_ENABLED", True)
    if engine == ENGINE_GOOGLE:
        return _env_bool("SEARCH_GOOGLE_ENABLED", True)
    return False


def daily_limit(engine: str) -> int:
    if engine == ENGINE_BAIDU:
        return _env_int("SEARCH_BAIDU_DAILY_LIMIT", 100)
    if engine == ENGINE_BAIKE:
        return _env_int("SEARCH_BAIKE_DAILY_LIMIT", 100)
    if engine == ENGINE_GOOGLE:
        return _env_int("SEARCH_GOOGLE_DAILY_LIMIT", 100)
    return 0


def _connect():
    import psycopg
    from psycopg.rows import dict_row

    url = _pg_url()
    if not url:
        raise RuntimeError("未配置 OFFICE_PG_URL，无法持久化搜索日计数")
    # 短超时，避免 Postgres 不可达时拖死 mcp-server 启动
    return psycopg.connect(url, row_factory=dict_row, connect_timeout=3)


def ensure_schema() -> None:
    """建表（幂等）。不可达时抛错，由调用方决定是否阻塞启动。"""
    global _ensured
    with _lock:
        if _ensured:
            return
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS external_search_daily (
                        engine TEXT NOT NULL,
                        day DATE NOT NULL,
                        count INT NOT NULL DEFAULT 0,
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        PRIMARY KEY (engine, day)
                    )
                    """
                )
            conn.commit()
        _ensured = True


def get_count(engine: str, day: str | None = None) -> int:
    ensure_schema()
    d = day or today_str()
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT count FROM external_search_daily WHERE engine = %s AND day = %s::date",
                (engine, d),
            )
            row = cur.fetchone()
    return int(row["count"]) if row else 0


def status(engine: str) -> dict[str, Any]:
    """开关 + 今日已用 + 上限；达上限则视为自动关闭。"""
    enabled_flag = engine_enabled(engine)
    limit = daily_limit(engine)
    used = 0
    store_error = ""
    try:
        used = get_count(engine)
    except Exception as exc:  # noqa: BLE001
        store_error = str(exc)
    at_limit = limit > 0 and used >= limit
    # limit=0 表示禁止调用
    auto_off = (not enabled_flag) or limit <= 0 or at_limit
    return {
        "engine": engine,
        "enabled": enabled_flag and not at_limit and limit > 0,
        "switch_on": enabled_flag,
        "auto_closed": at_limit or limit <= 0,
        "used_today": used,
        "daily_limit": limit,
        "day": today_str(),
        "store_error": store_error or None,
        "closed_reason": (
            None
            if not auto_off
            else (
                "switch_off"
                if not enabled_flag
                else ("limit_zero" if limit <= 0 else "daily_limit_reached")
            )
        ),
    }


def try_consume(engine: str) -> dict[str, Any]:
    """若允许则原子 +1 并返回 ok；否则返回 error 与 status。"""
    st = status(engine)
    if st.get("store_error"):
        return {
            "ok": False,
            "error": f"搜索计数存储不可用: {st['store_error']}",
            **st,
        }
    if not st["switch_on"]:
        return {
            "ok": False,
            "error": f"{engine} 搜索已关闭（SEARCH_{engine.upper()}_ENABLED=0）",
            **st,
        }
    if st["daily_limit"] <= 0:
        return {
            "ok": False,
            "error": f"{engine} 搜索日限额为 0，拒绝调用",
            **st,
        }
    if st["used_today"] >= st["daily_limit"]:
        return {
            "ok": False,
            "error": (
                f"{engine} 今日已达上限 {st['daily_limit']} 次，已自动关闭；"
                f"已用 {st['used_today']}（{st['day']}）"
            ),
            **{**st, "enabled": False, "auto_closed": True},
        }

    ensure_schema()
    d = today_str()
    with _lock:
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO external_search_daily (engine, day, count, updated_at)
                    VALUES (%s, %s::date, 1, NOW())
                    ON CONFLICT (engine, day) DO UPDATE
                    SET count = external_search_daily.count + 1,
                        updated_at = NOW()
                    WHERE external_search_daily.count < %s
                    RETURNING count
                    """,
                    (engine, d, st["daily_limit"]),
                )
                row = cur.fetchone()
            conn.commit()
    if not row:
        used = get_count(engine)
        return {
            "ok": False,
            "error": f"{engine} 今日已达上限 {st['daily_limit']} 次，已自动关闭；已用 {used}",
            "engine": engine,
            "enabled": False,
            "switch_on": st["switch_on"],
            "auto_closed": True,
            "used_today": used,
            "daily_limit": st["daily_limit"],
            "day": d,
            "closed_reason": "daily_limit_reached",
        }
    return {
        "ok": True,
        "engine": engine,
        "enabled": True,
        "switch_on": True,
        "auto_closed": False,
        "used_today": int(row["count"]),
        "daily_limit": st["daily_limit"],
        "day": d,
    }
