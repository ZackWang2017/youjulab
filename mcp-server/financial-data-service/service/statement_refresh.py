"""检查财报表里已有证券的报表是否落后于远程，并补齐新报告期或缺失的报表。

只处理 financial_statements 中已经出现过的代码。最新报告期缺资产负债表、利润表、
现金流量表之一，或远程已经发布了更新的报告期时，复用现有同步整表覆盖写入。
同一报告期再次写入会按唯一键更新，不会插入重复行。

用法（在 mcp-server 目录下）：
  ../.venv/bin/python financial-data-service/service/statement_refresh.py
  ../.venv/bin/python financial-data-service/service/statement_refresh.py --dry-run
  ../.venv/bin/python financial-data-service/service/statement_refresh.py --ticker 600519.SH
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Any

import httpx

SERVICE_DIR = Path(__file__).resolve().parent
REPO_DIR = SERVICE_DIR.parent / "repository"
MCP_ROOT = SERVICE_DIR.parents[1]
for _path in (str(MCP_ROOT), str(REPO_DIR), str(SERVICE_DIR)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import a_share_statement_repository as a_share  # noqa: E402
import eastmoney_repository as em  # noqa: E402
from statement_repository import list_latest_statement_coverage  # noqa: E402

PERIOD_RANK = {"1季报": 1, "2季报": 2, "3季报": 3, "4季报": 4, "年报": 5}
REQUIRED_CATEGORIES = ("资产负债表", "利润表", "现金流量表")
SUPPORTED_MARKETS = {"SH", "SZ", "BJ", "HK"}

ACTION_SKIP = "已是最新"
ACTION_REFRESH = "已补齐"
ACTION_DRY = "将补齐"
ACTION_FAIL = "失败"
ACTION_UNSUPPORTED = "跳过"


def period_is_newer(left: tuple[int, str], right: tuple[int, str]) -> bool:
    """left 是否晚于 right。同年内年报晚于三季报。"""
    return (left[0], PERIOD_RANK.get(left[1], 0)) > (right[0], PERIOD_RANK.get(right[1], 0))


def period_label(period: tuple[int, str]) -> str:
    return f"{period[0]}年{period[1]}"


def period_from_em_row(row: dict) -> tuple[int, str] | None:
    report_dt = em.parse_report_date(row.get("REPORT_DATE"))
    if report_dt is None:
        return None
    report_type = str(row.get("REPORT_TYPE") or "").strip()
    if report_type:
        period = a_share.EM_PERIOD.get(report_type)
        if period:
            return report_dt.year, period
    date_type = str(row.get("DATE_TYPE_CODE") or "").strip()
    period = em.DATE_TYPE_TO_PERIOD.get(date_type)
    if period:
        return report_dt.year, period
    return None


def latest_from_sina_payload(payload: dict) -> tuple[int, str] | None:
    data = ((payload.get("result") or {}).get("data") or {})
    best: tuple[int, str] | None = None
    for item in data.get("report_date") or []:
        date_value = str(item.get("date_value") or "")
        try:
            date_type = int(item.get("date_type"))
        except (TypeError, ValueError):
            continue
        period = a_share.SINA_PERIOD.get(date_type)
        if period is None or len(date_value) < 4 or not date_value[:4].isdigit():
            continue
        current = (int(date_value[:4]), period)
        if best is None or period_is_newer(current, best):
            best = current
    return best


def decide_refresh(
    *,
    market: str,
    categories: set[str],
    local: tuple[int, str],
    remote: tuple[int, str] | None,
    probe_error: str | None,
    force: bool,
) -> tuple[str, str]:
    """返回 (action, reason)。action 为 refresh / skip / fail / unsupported。"""
    if market not in SUPPORTED_MARKETS:
        return "unsupported", f"暂不补齐 {market} 市场财报"
    if force:
        return "refresh", "强制补齐已入库财报"
    missing = [name for name in REQUIRED_CATEGORIES if name not in categories]
    if missing:
        return "refresh", f"最新报告期 {period_label(local)} 缺少{'、'.join(missing)}"
    if probe_error:
        return "fail", probe_error
    if remote is None:
        return "skip", f"本地 {period_label(local)}，远程没有返回更新的报告期"
    if period_is_newer(remote, local):
        return "refresh", f"远程最新为 {period_label(remote)}，本地最新为 {period_label(local)}"
    return "skip", f"本地 {period_label(local)}"


def probe_eastmoney_latest(
    ticker_code: str,
    market: str,
    *,
    client: em.EastMoneyHkClient,
) -> tuple[int, str] | None:
    reports = a_share.A_SHARE_REPORTS if market in a_share.A_SHARE_MARKETS else em.STATEMENT_REPORTS
    best: tuple[int, str] | None = None
    errors: list[str] = []
    for report_name in reports.values():
        try:
            row = client.fetch_latest_row(report_name=report_name, secucode=ticker_code)
        except RuntimeError as exc:
            errors.append(str(exc))
            continue
        if not row:
            continue
        period = period_from_em_row(row)
        if period and (best is None or period_is_newer(period, best)):
            best = period
    if best is None and errors and len(errors) == len(reports):
        raise RuntimeError(errors[0])
    return best


def probe_sina_latest(ticker_code: str, market: str, *, timeout: float = 30.0) -> tuple[int, str] | None:
    paper = a_share.sina_paper_code(ticker_code, market)
    headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn"}
    best: tuple[int, str] | None = None
    errors: list[str] = []
    with httpx.Client(timeout=timeout, headers=headers, trust_env=False) as client:
        for source in a_share.SINA_SOURCES.values():
            try:
                response = client.get(
                    a_share.SINA_URL,
                    params={"paperCode": paper, "source": source, "type": 0, "page": 1, "num": 30},
                )
                response.raise_for_status()
                payload = response.json()
            except (httpx.HTTPError, ValueError) as exc:
                errors.append(str(exc))
                continue
            status = ((payload.get("result") or {}).get("status") or {}).get("code")
            if status not in (0, "0", None):
                errors.append(f"{source} code={status}")
                continue
            period = latest_from_sina_payload(payload)
            if period and (best is None or period_is_newer(period, best)):
                best = period
    if best is None and errors and len(errors) == len(a_share.SINA_SOURCES):
        raise RuntimeError(errors[0])
    return best


def _probe(ticker_code: str, market: str) -> tuple[tuple[int, str] | None, str | None, str | None]:
    """返回 (远程最新报告期, 错误, 探测来源)。探测来源只在东方财富失败、新浪成功时为新浪。"""
    try:
        with em.EastMoneyHkClient() as client:
            return probe_eastmoney_latest(ticker_code, market, client=client), None, None
    except (RuntimeError, httpx.HTTPError) as exc:
        if market not in a_share.A_SHARE_MARKETS:
            return None, f"检查远程财报失败：{exc}", None
        try:
            return probe_sina_latest(ticker_code, market), None, a_share.DATA_SOURCE_SINA
        except (RuntimeError, httpx.HTTPError) as sina_exc:
            return None, f"检查远程财报失败：东方财富 {exc}；新浪财经 {sina_exc}", None


def _sync(ticker_code: str, market: str, *, source: str) -> dict[str, Any]:
    if market == "HK":
        return em.sync_hk_financial_statements(ticker_code)
    return a_share.sync_a_share_financial_statements(ticker_code, source=source)


def refresh_coverage_row(row: dict[str, Any], *, dry_run: bool, force: bool) -> dict[str, Any]:
    ticker_code = str(row["ticker_code"])
    market = str(row["market_code"])
    local = (int(row["fiscal_year"]), str(row["report_period"]))
    categories = set(row.get("categories") or [])
    remote: tuple[int, str] | None = None
    probe_error: str | None = None
    probe_source: str | None = None
    if market in SUPPORTED_MARKETS and not force:
        remote, probe_error, probe_source = _probe(ticker_code, market)
    action, reason = decide_refresh(
        market=market,
        categories=categories,
        local=local,
        remote=remote,
        probe_error=probe_error,
        force=force,
    )
    result = {
        "ticker_code": ticker_code,
        "market_code": market,
        "action": action,
        "reason": reason,
        "saved": 0,
        "data_source": "",
    }
    if action != "refresh":
        if action == "skip":
            result["action"] = ACTION_SKIP
        elif action == "fail":
            result["action"] = ACTION_FAIL
        else:
            result["action"] = ACTION_UNSUPPORTED
        return result
    if dry_run:
        result["action"] = ACTION_DRY
        return result
    source = "sina" if probe_source == a_share.DATA_SOURCE_SINA else "auto"
    try:
        summary = _sync(ticker_code, market, source=source)
    except Exception as exc:  # noqa: BLE001
        result["action"] = ACTION_FAIL
        result["reason"] = f"{reason}；写入失败：{exc}"
        return result
    result["action"] = ACTION_REFRESH
    result["saved"] = int(summary.get("saved") or 0)
    result["data_source"] = str(summary.get("data_source") or "")
    return result


def refresh_existing_statements(
    tickers: list[str] | None = None,
    *,
    dry_run: bool = False,
    force: bool = False,
) -> list[dict[str, Any]]:
    rows = list_latest_statement_coverage(tickers)
    results: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        results.append(refresh_coverage_row(row, dry_run=dry_run, force=force))
        if index + 1 < len(rows):
            time.sleep(em.EM_REQUEST_PAUSE)
    return results


def _print_result(item: dict[str, Any]) -> None:
    extra = ""
    if item["action"] == ACTION_REFRESH:
        extra = f"  {item['data_source']} 写入 {item['saved']}"
    print(f"{item['ticker_code']}  {item['action']}  {item['reason']}{extra}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Refresh incomplete financial statements already stored")
    parser.add_argument("--ticker", action="append", default=[], help="只检查这些代码，可重复。默认检查表内全部")
    parser.add_argument("--dry-run", action="store_true", help="只列出将补齐的证券，不写数据库")
    parser.add_argument("--force", action="store_true", help="不比较报告期，重新抓取表内已有证券")
    args = parser.parse_args(argv)
    tickers = [item.strip().upper() for item in args.ticker if item.strip()] or None
    try:
        results = refresh_existing_statements(tickers, dry_run=args.dry_run, force=args.force)
    except Exception as exc:  # noqa: BLE001
        print(f"refresh failed: {exc}", file=sys.stderr)
        return 1
    if not results:
        if tickers:
            print("这些代码在财报表里还没有数据：" + "、".join(tickers))
        else:
            print("财报表里没有可检查的证券。")
        return 0
    for item in results:
        _print_result(item)
    counts: dict[str, int] = {}
    for item in results:
        counts[item["action"]] = counts.get(item["action"], 0) + 1
    print(
        "合计 "
        + "，".join(f"{name} {count}" for name, count in counts.items())
    )
    if any(item["action"] == ACTION_FAIL for item in results):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
