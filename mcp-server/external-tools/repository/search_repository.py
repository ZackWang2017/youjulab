"""外部搜索引擎适配：百度千帆 web_search + 百度百科站点搜 + Google Custom Search JSON API。

配置模型（Google 无法全网搜时）：
  - 只用一个 GOOGLE_CSE_CX，控制台收录「全部垂直站点」（三组 SEARCH_SITES_* 的并集）。
  - topic 为空：在整份 CSE 收录范围内搜。
  - topic 非空：仍用同一 CX，再用对应 SEARCH_SITES_* 做 site: / 百度 site 过滤收窄。
  - baidu_baike_search：百科词条接口 lemma/get_content（与 web_search 分开，独立开关/日限额）。

调用前经 usage 模块：开关 + 每日次数；达上限自动拒绝。
"""
from __future__ import annotations

import os
from typing import Any

import httpx

import usage

BAIDU_WEB_SEARCH_URL = "https://qianfan.baidubce.com/v2/ai_search/web_search"
GOOGLE_CSE_URL = "https://www.googleapis.com/customsearch/v1"
# 百科词条接口，与千帆 web_search 不是同一类查询。
BAIKE_CONTENT_URL = "https://appbuilder.baidu.com/v2/baike/lemma/get_content"
BAIKE_LIST_URL = "https://appbuilder.baidu.com/v2/baike/lemma/get_list_by_title"

_DEFAULT_SITES: dict[str, list[str]] = {
    "finance": [
        "finance.eastmoney.com",
        "www.cninfo.com.cn",
        "www.sse.com.cn",
        "www.szse.cn",
        "www.cls.cn",
        "finance.sina.com.cn",
        "www.stcn.com",
        "www.cs.com.cn",
        "www.yicai.com",
        "www.wallstreetcn.com",
        "www.investing.com",
        "finance.yahoo.com",
    ],
    "accounting": [
        "kjs.mof.gov.cn",
        "www.casc.org.cn",
        "www.cicpa.org.cn",
        "www.mof.gov.cn",
        "www.csrc.gov.cn",
        "www.ifrs.org",
        "www.fasb.org",
        "www.sec.gov",
    ],
    "encyclopedia": [
        "baike.baidu.com",
        "zh.wikipedia.org",
        "en.wikipedia.org",
        "wiki.mbalib.com",
        "www.investopedia.com",
    ],
}

_ENV_SITES_KEY = {
    "finance": "SEARCH_SITES_FINANCE",
    "accounting": "SEARCH_SITES_ACCOUNTING",
    "encyclopedia": "SEARCH_SITES_ENCYCLOPEDIA",
}

_TOPIC_KEYS = ("finance", "accounting", "encyclopedia")

TOPIC_OR_TERMS: dict[str, str] = {
    "finance": "财务 OR 财报 OR 投资 OR 证券",
    "accounting": "会计 OR 会计准则 OR 审计",
    "encyclopedia": "百科 OR 定义 OR 概念",
}

VALID_TOPICS = frozenset({"", "finance", "accounting", "encyclopedia"})

_BAIDU_SITE_CAP = 20
_GOOGLE_SITE_QUERY_CAP = 10


def _parse_sites(raw: str) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for part in (raw or "").replace(";", ",").split(","):
        host = part.strip().lower()
        if not host:
            continue
        if "://" in host:
            host = host.split("://", 1)[1]
        host = host.split("/", 1)[0].strip()
        if host and host not in seen:
            seen.add(host)
            out.append(host)
    return out


def _merge_sites(*groups: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for group in groups:
        for host in group:
            if host and host not in seen:
                seen.add(host)
                out.append(host)
    return out


def topic_sites(topic: str) -> list[str]:
    if not topic:
        return []
    env_key = _ENV_SITES_KEY.get(topic)
    if env_key:
        from_env = _parse_sites(os.environ.get(env_key) or "")
        if from_env:
            return from_env
    return list(_DEFAULT_SITES.get(topic) or [])


def all_sites() -> list[str]:
    explicit = _parse_sites(os.environ.get("SEARCH_SITES") or "")
    if explicit:
        return explicit
    return _merge_sites(*(topic_sites(t) for t in _TOPIC_KEYS))


def _norm_topic(topic: str | None) -> str:
    t = (topic or "").strip().lower()
    if t not in VALID_TOPICS:
        raise ValueError(
            f"不支持的 topic={topic!r}；可选：空、finance、accounting、encyclopedia"
        )
    return t


def _clamp_num(num: int) -> int:
    try:
        n = int(num)
    except (TypeError, ValueError):
        n = 5
    return max(1, min(n, 10))


def _google_cx() -> str:
    return (os.environ.get("GOOGLE_CSE_CX") or "").strip()


def _google_query_with_sites(query: str, sites: list[str]) -> str:
    if not sites:
        return query
    clipped = sites[:_GOOGLE_SITE_QUERY_CAP]
    site_clause = " OR ".join(f"site:{s}" for s in clipped)
    return f"({query}) ({site_clause})"


def _quota_dict(st: dict[str, Any]) -> dict[str, Any]:
    return {
        "enabled": st.get("enabled"),
        "switch_on": st.get("switch_on"),
        "auto_closed": st.get("auto_closed"),
        "used_today": st.get("used_today"),
        "daily_limit": st.get("daily_limit"),
        "day": st.get("day"),
        "closed_reason": st.get("closed_reason"),
    }


def _refuse_quota(engine: str, query: str, st: dict[str, Any] | None = None) -> dict[str, Any]:
    st = st or usage.status(engine)
    reason = st.get("closed_reason")
    if reason == "switch_off":
        err = f"{engine} 搜索已关闭（SEARCH_{engine.upper()}_ENABLED=0）"
    elif reason == "limit_zero":
        err = f"{engine} 搜索日限额为 0，拒绝调用"
    elif reason == "daily_limit_reached":
        err = (
            f"{engine} 今日已达上限 {st.get('daily_limit')} 次，已自动关闭；"
            f"已用 {st.get('used_today')}（{st.get('day')}）"
        )
    elif st.get("store_error"):
        err = f"搜索计数存储不可用: {st['store_error']}"
    else:
        err = f"{engine} 搜索不可用"
    return {
        "error": err,
        "engine": engine,
        "query": query,
        "results": [],
        "quota": _quota_dict(st),
    }


def _attach_quota(payload: dict[str, Any], engine: str, st: dict[str, Any] | None = None) -> dict[str, Any]:
    payload["quota"] = _quota_dict(st or usage.status(engine))
    return payload


def _begin_search(engine: str, query: str) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """返回 (error_payload, consume_status)。允许调用时先 status 再 try_consume。"""
    st = usage.status(engine)
    if st.get("store_error") or not st.get("enabled"):
        return _refuse_quota(engine, query, st), None
    gate = usage.try_consume(engine)
    if not gate.get("ok"):
        return _refuse_quota(engine, query, gate), None
    return None, gate


def _finish(payload: dict[str, Any], engine: str, gate: dict[str, Any] | None, apply_quota: bool) -> dict[str, Any]:
    if not apply_quota:
        return payload
    return _attach_quota(payload, engine, gate)


def _qianfan_web_search(
    *,
    engine: str,
    query: str,
    num: int,
    sites: list[str],
    label: str,
    extra: dict[str, Any] | None = None,
    apply_quota: bool = True,
) -> dict[str, Any]:
    """千帆 AI Search 公共调用。sites 非空时做 site 过滤。"""
    q = (query or "").strip()
    if not q:
        return {"error": "query 不能为空", "engine": engine, "query": q, "results": []}
    n = _clamp_num(num)
    api_key = (os.environ.get("BAIDU_QIANFAN_API_KEY") or "").strip()
    if not api_key:
        return _finish(
            {
                "error": f"未配置 BAIDU_QIANFAN_API_KEY，无法调用{label}",
                "engine": engine,
                "query": q,
                "results": [],
                **(extra or {}),
            },
            engine,
            None,
            apply_quota,
        )
    gate = None
    if apply_quota:
        refused, gate = _begin_search(engine, q)
        if refused:
            return refused

    body: dict[str, Any] = {
        "messages": [{"role": "user", "content": q}],
        "search_source": "baidu_search_v2",
        "resource_type_filter": [{"type": "web", "top_k": n}],
    }
    if sites:
        body["search_filter"] = {"match": {"site": sites[:_BAIDU_SITE_CAP]}}

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
        "X-Appbuilder-Authorization": f"Bearer {api_key}",
    }
    try:
        with httpx.Client(timeout=30.0) as client:
            resp = client.post(BAIDU_WEB_SEARCH_URL, headers=headers, json=body)
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPStatusError as exc:
        detail = ""
        try:
            detail = exc.response.text[:500]
        except Exception:
            pass
        return _finish(
            {
                "error": f"{label} HTTP {exc.response.status_code}: {detail or exc}",
                "engine": engine,
                "query": q,
                "results": [],
                **(extra or {}),
            },
            engine,
            gate,
            apply_quota,
        )
    except Exception as exc:  # noqa: BLE001
        return _finish(
            {
                "error": f"{label}失败: {exc}",
                "engine": engine,
                "query": q,
                "results": [],
                **(extra or {}),
            },
            engine,
            gate,
            apply_quota,
        )

    if isinstance(data, dict) and data.get("code") and not data.get("references"):
        return _finish(
            {
                "error": f"{label}错误: {data.get('message') or data.get('code')}",
                "engine": engine,
                "query": q,
                "results": [],
                **(extra or {}),
            },
            engine,
            gate,
            apply_quota,
        )

    results = []
    for item in (data.get("references") or []) if isinstance(data, dict) else []:
        if (item.get("type") or "web") not in ("web", None, ""):
            continue
        results.append(
            {
                "title": item.get("title") or "",
                "link": item.get("url") or "",
                "snippet": item.get("snippet") or item.get("content") or "",
            }
        )
        if len(results) >= n:
            break

    return _finish(
        {
            "engine": engine,
            "query": q,
            "sites": list(sites[:_BAIDU_SITE_CAP]) if sites else [],
            "results": results,
            **(extra or {}),
        },
        engine,
        gate,
        apply_quota,
    )


def baidu_web_search(query: str, num: int = 5, topic: str = "", *, apply_quota: bool = True) -> dict[str, Any]:
    """百度千帆 AI 搜索（web_search），返回标题/链接/摘要列表。"""
    q = (query or "").strip()
    if not q:
        return {"error": "query 不能为空", "engine": "baidu", "query": q, "results": []}
    try:
        topic_n = _norm_topic(topic)
    except ValueError as exc:
        return {"error": str(exc), "engine": "baidu", "query": q, "results": []}
    sites = topic_sites(topic_n)[:_BAIDU_SITE_CAP] if topic_n else []
    return _qianfan_web_search(
        engine=usage.ENGINE_BAIDU,
        query=q,
        num=num,
        sites=sites,
        label="百度搜索",
        extra={"topic": topic_n},
        apply_quota=apply_quota,
    )


def _baike_headers(api_key: str) -> dict[str, str]:
    token = f"Bearer {api_key}"
    return {
        "Content-Type": "application/json",
        "Authorization": token,
        "X-Appbuilder-Authorization": token,
    }


def _baike_items(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if not isinstance(data, dict):
        return []
    code = data.get("code")
    if code not in (None, "", 0, "0") and not data.get("result"):
        return []
    result = data.get("result", data)
    if isinstance(result, list):
        return [item for item in result if isinstance(item, dict)]
    if isinstance(result, dict) and (result.get("lemma_title") or result.get("summary") or result.get("url")):
        return [result]
    return []


def _baike_result_row(item: dict[str, Any]) -> dict[str, str]:
    title = str(item.get("lemma_title") or item.get("title") or "").strip()
    desc = str(item.get("lemma_desc") or "").strip()
    if desc and title:
        title = f"{title}（{desc}）"
    summary = str(item.get("summary") or item.get("lemma_desc") or "").strip()
    return {
        "title": title,
        "link": str(item.get("url") or "").strip(),
        "snippet": summary,
    }


def baidu_baike_search(query: str, num: int = 5, *, apply_quota: bool = True) -> dict[str, Any]:
    """百度百科词条查询（lemma/get_content），不是百度网页搜索。"""
    q = (query or "").strip()
    if not q:
        return {"error": "query 不能为空", "engine": "baike", "query": q, "results": []}
    n = _clamp_num(num)
    api_key = (os.environ.get("BAIDU_QIANFAN_API_KEY") or "").strip()
    if not api_key:
        return _finish(
            {
                "error": "未配置 BAIDU_QIANFAN_API_KEY，无法调用百度百科",
                "engine": "baike",
                "query": q,
                "results": [],
            },
            usage.ENGINE_BAIKE,
            None,
            apply_quota,
        )
    gate = None
    if apply_quota:
        refused, gate = _begin_search(usage.ENGINE_BAIKE, q)
        if refused:
            return refused

    headers = _baike_headers(api_key)
    try:
        with httpx.Client(timeout=30.0) as client:
            resp = client.get(
                BAIKE_CONTENT_URL,
                headers=headers,
                params={"search_type": "lemmaTitle", "search_key": q},
            )
            resp.raise_for_status()
            data = resp.json()
            items = _baike_items(data)
            if not items:
                listed = client.get(
                    BAIKE_LIST_URL,
                    headers=headers,
                    params={"lemma_title": q, "top_k": n},
                )
                listed.raise_for_status()
                items = _baike_items(listed.json())
    except httpx.HTTPStatusError as exc:
        detail = ""
        try:
            detail = exc.response.text[:500]
        except Exception:
            pass
        return _finish(
            {
                "error": f"百度百科 HTTP {exc.response.status_code}: {detail or exc}",
                "engine": "baike",
                "query": q,
                "results": [],
            },
            usage.ENGINE_BAIKE,
            gate,
            apply_quota,
        )
    except Exception as exc:  # noqa: BLE001
        return _finish(
            {
                "error": f"百度百科失败: {exc}",
                "engine": "baike",
                "query": q,
                "results": [],
            },
            usage.ENGINE_BAIKE,
            gate,
            apply_quota,
        )

    if isinstance(data, dict):
        code = data.get("code")
        if code not in (None, "", 0, "0") and not items:
            return _finish(
                {
                    "error": f"百度百科错误: {data.get('message') or code}",
                    "engine": "baike",
                    "query": q,
                    "results": [],
                },
                usage.ENGINE_BAIKE,
                gate,
                apply_quota,
            )

    results = [_baike_result_row(item) for item in items[:n] if _baike_result_row(item)["title"] or _baike_result_row(item)["snippet"]]
    return _finish(
        {"engine": "baike", "query": q, "results": results},
        usage.ENGINE_BAIKE,
        gate,
        apply_quota,
    )


def google_web_search(query: str, num: int = 5, topic: str = "", *, apply_quota: bool = True) -> dict[str, Any]:
    """Google Custom Search JSON API（单一 CX + 主题站点子集收窄）。"""
    q = (query or "").strip()
    if not q:
        return {"error": "query 不能为空", "engine": "google", "query": q, "results": []}
    try:
        topic_n = _norm_topic(topic)
    except ValueError as exc:
        return {"error": str(exc), "engine": "google", "query": q, "results": []}
    n = _clamp_num(num)
    api_key = (os.environ.get("GOOGLE_CSE_API_KEY") or "").strip()
    cx = _google_cx()
    if not api_key or not cx:
        return _finish(
            {
                "error": "未配置 GOOGLE_CSE_API_KEY / GOOGLE_CSE_CX，无法调用 Google 搜索",
                "engine": "google",
                "query": q,
                "topic": topic_n,
                "results": [],
            },
            usage.ENGINE_GOOGLE,
            None,
            apply_quota,
        )
    gate = None
    if apply_quota:
        refused, gate = _begin_search(usage.ENGINE_GOOGLE, q)
        if refused:
            return refused

    sites = topic_sites(topic_n) if topic_n else all_sites()
    search_q = q
    or_terms = None
    if topic_n:
        search_q = _google_query_with_sites(q, sites)
        or_terms = TOPIC_OR_TERMS.get(topic_n)

    params: dict[str, Any] = {
        "key": api_key,
        "cx": cx,
        "q": search_q,
        "num": n,
    }
    if or_terms:
        params["orTerms"] = or_terms

    try:
        with httpx.Client(timeout=30.0) as client:
            resp = client.get(GOOGLE_CSE_URL, params=params)
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPStatusError as exc:
        detail = ""
        try:
            detail = exc.response.text[:500]
        except Exception:
            pass
        return _finish(
            {
                "error": f"Google 搜索 HTTP {exc.response.status_code}: {detail or exc}",
                "engine": "google",
                "query": q,
                "topic": topic_n,
                "results": [],
            },
            usage.ENGINE_GOOGLE,
            gate,
            apply_quota,
        )
    except Exception as exc:  # noqa: BLE001
        return _finish(
            {
                "error": f"Google 搜索失败: {exc}",
                "engine": "google",
                "query": q,
                "topic": topic_n,
                "results": [],
            },
            usage.ENGINE_GOOGLE,
            gate,
            apply_quota,
        )

    results = []
    for item in (data.get("items") or []) if isinstance(data, dict) else []:
        results.append(
            {
                "title": item.get("title") or "",
                "link": item.get("link") or "",
                "snippet": item.get("snippet") or "",
            }
        )

    return _finish(
        {
            "engine": "google",
            "query": q,
            "topic": topic_n,
            "sites": sites,
            "cx": cx,
            "results": results,
        },
        usage.ENGINE_GOOGLE,
        gate,
        apply_quota,
    )
