"""资讯来源适配器清单。只描述已实现的适配器，不访问外网。"""
from __future__ import annotations

from typing import Any

FINANCE_API_ADAPTERS: list[dict[str, str]] = [
    {"id": "sina", "name": "新浪财经", "site": "finance.sina.com.cn", "icon": ""},
    {"id": "eastmoney", "name": "东方财富", "site": "eastmoney.com", "icon": ""},
    {"id": "xueqiu", "name": "雪球", "site": "xueqiu.com", "icon": ""},
    {"id": "jisilu", "name": "集思录", "site": "jisilu.cn", "icon": ""},
    {"id": "tencent", "name": "腾讯自选股", "site": "gu.qq.com", "icon": ""},
]

FINANCE_RSS_ADAPTERS: list[dict[str, str]] = [
    {"id": "wallstreetcn", "name": "华尔街见闻", "site": "wallstreetcn.com", "kind": "rss", "url": "https://dedicated.wallstreetcn.com/rss.xml", "icon": ""},
    {"id": "cls", "name": "财联社", "site": "cls.cn", "kind": "native", "icon": ""},
    {"id": "jin10", "name": "金十数据", "site": "jin10.com", "kind": "native", "icon": ""},
    {"id": "caixin", "name": "财新网", "site": "caixin.com", "kind": "native", "icon": ""},
    {"id": "stcn", "name": "证券时报", "site": "stcn.com", "kind": "native", "icon": ""},
    {"id": "36kr", "name": "36氪", "site": "36kr.com", "kind": "native", "icon": ""},
]

ANNOUNCE_ADAPTERS: list[dict[str, Any]] = [
    {"id": "cninfo", "name": "巨潮资讯", "site": "cninfo.com.cn", "markets": ["SH", "SZ", "BJ"], "icon": ""},
    {"id": "sse", "name": "上海证券交易所", "site": "sse.com.cn", "markets": ["SH"], "icon": ""},
    {"id": "szse", "name": "深圳证券交易所", "site": "szse.cn", "markets": ["SZ"], "icon": ""},
    {"id": "hkex", "name": "香港交易所", "site": "hkexnews.hk", "markets": ["HK"], "icon": ""},
    {"id": "sec", "name": "SEC EDGAR", "site": "sec.gov", "markets": ["US"], "icon": ""},
]

KIND_LABELS = {
    "wechat": "公众号",
    "finance_api": "财经 API",
    "finance_rss": "财经 RSS",
    "holdings_announce": "持仓公告",
    "favorites": "收藏夹",
}

DEFAULT_ANNOUNCE = {"SH": "cninfo", "SZ": "cninfo", "BJ": "cninfo", "HK": "hkex", "US": "sec"}


def list_adapters(kind: str) -> list[dict[str, Any]]:
    if kind == "finance_api":
        return [dict(item, kind="finance_api") for item in FINANCE_API_ADAPTERS]
    if kind == "finance_rss":
        return [dict(item, kind="finance_rss") for item in FINANCE_RSS_ADAPTERS]
    if kind == "holdings_announce":
        return [dict(item, kind="holdings_announce") for item in ANNOUNCE_ADAPTERS]
    return []


def finance_api(adapter_id: str) -> dict[str, str] | None:
    return next((item for item in FINANCE_API_ADAPTERS if item["id"] == adapter_id), None)


def finance_rss(adapter_id: str) -> dict[str, str] | None:
    return next((item for item in FINANCE_RSS_ADAPTERS if item["id"] == adapter_id), None)


def announce(adapter_id: str) -> dict[str, Any] | None:
    return next((item for item in ANNOUNCE_ADAPTERS if item["id"] == adapter_id), None)


def adapters_for_market(market_code: str) -> list[dict[str, Any]]:
    market = (market_code or "").upper()
    return [dict(item) for item in ANNOUNCE_ADAPTERS if market in item["markets"]]


def default_announce_adapter(market_code: str) -> str:
    return DEFAULT_ANNOUNCE.get((market_code or "").upper(), "cninfo")
