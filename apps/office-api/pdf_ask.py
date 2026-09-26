"""PDF 选区问 AI：有界 tool-call（语义检索 + 百度/百科/Google）后流式 Markdown 回答。"""
from __future__ import annotations

import json
import re
import sys
from collections.abc import AsyncIterator
from typing import Any

from config import ROOT, model_configured, model_name
from assistant import iter_chat_events
from office_pdf_vector import ensure_index, get_index_status, search_chunks

MAX_SELECTION = 8000
MAX_QUESTION = 2000
MAX_HISTORY = 20
MAX_TURN = 24000
MAX_TOOL_ROUNDS = 6
MAX_SEARCH_PER_ROUND = 2
MAX_WEB_PER_ROUND = 3

_WEB_SEARCH_TOOLS = frozenset({"baidu_web_search", "baidu_baike_search", "google_web_search"})
_PDF_TOOLS = frozenset({"ensure_office_pdf_index", "get_office_pdf_index_status", "search_office_pdf"})
_DOC_RE = re.compile(
    r"本文档|本财报|本报告|这份财报|这份年报|这份季报|这份报告|该财报|该年报|该季报|"
    r"20\d{2}\s*年.{0,12}(财报|年报|季报|中报|半年报)|"
    r"(第?[一二三四1-4]\s*季度|Q[1-4]|[一二三四1-4]季报).{0,8}(财报|报告|年报)"
)
_ANALYSIS_RE = re.compile(r"请分析|分析一下|为什么|讨论一下|讨论下")
_EMPTY_SECTION = re.compile(
    r"^(无|无。|无（已有外部搜索结果）|无（已有外部搜索结果）。|本文无相关段落|本文无相关段落。)$"
)
_DROP_IF_EMPTY = frozenset({"本文搜索", "百度搜索", "百度百科", "Google搜索", "模型搜索"})

_MCP = ROOT / "mcp-server"
if str(_MCP) not in sys.path:
    sys.path.insert(0, str(_MCP))


def _web_search():
    from external_tools import search_engine  # noqa: WPS433

    return search_engine


PDF_ASK_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "ensure_office_pdf_index",
            "description": "Ensure the office PDF is parsed once into the vector index. Call when unsure if indexed.",
            "parameters": {
                "type": "object",
                "properties": {
                    "document_id": {"type": "string", "description": "Office PDF asset id"},
                },
                "required": ["document_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_office_pdf_index_status",
            "description": "Read PDF vector index status (ready/pending/failed/missing).",
            "parameters": {
                "type": "object",
                "properties": {
                    "document_id": {"type": "string"},
                },
                "required": ["document_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_office_pdf",
            "description": "Semantic search over the indexed office PDF. Use the user question plus selection keywords.",
            "parameters": {
                "type": "object",
                "properties": {
                    "document_id": {"type": "string"},
                    "query": {"type": "string"},
                    "top_k": {"type": "integer", "minimum": 1, "maximum": 20},
                },
                "required": ["document_id", "query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "baidu_web_search",
            "description": "Baidu web search for Chinese facts. Optional topic: finance|accounting|encyclopedia.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "num": {"type": "integer", "minimum": 1, "maximum": 10},
                    "topic": {"type": "string"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "baidu_baike_search",
            "description": "Query a Baidu Baike lemma by title. This is the encyclopedia entry API, not Baidu web search.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "num": {"type": "integer", "minimum": 1, "maximum": 10},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "google_web_search",
            "description": "Google CSE search. Optional topic: finance|accounting|encyclopedia.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "num": {"type": "integer", "minimum": 1, "maximum": 10},
                    "topic": {"type": "string"},
                },
                "required": ["query"],
            },
        },
    },
]


def ask_mode(question: str) -> str:
    """document：检索本地 PDF；analysis：深入分析但不查本地库；concept：只查外部并简答。"""
    text = question or ""
    if _DOC_RE.search(text):
        return "document"
    if _ANALYSIS_RE.search(text):
        return "analysis"
    return "concept"


def _tools_for(mode: str) -> list[dict]:
    if mode == "document":
        return PDF_ASK_TOOLS
    return [tool for tool in PDF_ASK_TOOLS if tool["function"]["name"] in _WEB_SEARCH_TOOLS]


def omit_empty_search_sections(content: str) -> str:
    """没有内容的搜索节不输出。模型搜索只在三个外部来源都空时保留。"""
    parts = re.split(r"(?=^##\s+)", content or "", flags=re.M)
    kept: list[tuple[str, str]] = []
    web_has_body = False
    for part in parts:
        if not part:
            continue
        matched = re.match(r"^##\s+(\S+)\s*\n?", part)
        if not matched:
            kept.append(("", part))
            continue
        title = matched.group(1)
        body = part[matched.end():].strip()
        if title in _DROP_IF_EMPTY and (not body or _EMPTY_SECTION.match(body)):
            continue
        if title in {"百度搜索", "百度百科", "Google搜索"} and body:
            web_has_body = True
        kept.append((title, part if part.endswith("\n") else part + "\n"))
    if web_has_body:
        kept = [(title, part) for title, part in kept if title != "模型搜索"]
    return "".join(part for _, part in kept).strip()


def _pdf_ask_system(mode: str) -> str:
    skill = (ROOT / "skills/pdf/SKILL.md").read_text()
    common = ROOT / "skills/common/external-tools.md"
    extra = common.read_text() if common.is_file() else ""
    if mode == "document":
        route = (
            "本轮是文档题。可以检索当前 PDF，并按需要调用 baidu_baike_search、"
            "baidu_web_search、google_web_search。baidu_baike_search 是百科词条，不是百度网页搜索。"
        )
    elif mode == "analysis":
        route = (
            "本轮是分析题。不要调用 PDF 向量检索。"
            "用百科、百度搜索、Google 收集依据后做完整推理。"
            "baidu_baike_search 是百科词条，不是百度网页搜索。"
        )
    else:
        route = (
            "本轮是概念题。不要调用 PDF 向量检索。"
            "只使用 baidu_baike_search、baidu_web_search、google_web_search。"
            "概念优先百科词条。回答保持简短：概念、计量或计算方法、会计上如何记账核算。"
        )
    return (
        skill
        + "\n\n"
        + extra
        + "\n\n"
        + "当前任务是「问AI」。"
        + route
        + "没有内容的搜索节不要写。三个外部来源都没有内容时，才写「模型搜索」并用自身知识回答。"
        "一律用简体中文；繁体材料转写为简体。不要编造页码、数字或链接。"
        "不要输出 JSON，不要输出 replacement。不要调用 parse_pdf。"
    )


def compose_answer(reasoning: str, content: str) -> str:
    """终答正文。思考过程单独走 reasoning，不写入正文。"""
    del reasoning
    return omit_empty_search_sections(content or "")


def _validate_history(history: list[dict]) -> list[dict]:
    if not isinstance(history, list) or len(history) > MAX_HISTORY:
        raise ValueError(f"问答历史最多 {MAX_HISTORY} 轮。")
    cleaned: list[dict] = []
    for item in history:
        if not isinstance(item, dict):
            raise ValueError("问答历史格式无效。")
        role = item.get("role")
        content = (item.get("content") or "").strip()
        if role not in ("user", "assistant"):
            raise ValueError("问答历史角色无效。")
        if not content or len(content) > MAX_TURN:
            raise ValueError("问答历史内容无效。")
        cleaned.append({"role": role, "content": content})
    return cleaned


def _tool_result(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False)


def _assistant_message(message: dict) -> dict:
    """DeepSeek tool+thinking：必须回传 reasoning_content。"""
    item: dict[str, Any] = {
        "role": "assistant",
        "content": message.get("content"),
    }
    reasoning = message.get("reasoning_content")
    if reasoning:
        item["reasoning_content"] = reasoning
    tool_calls = message.get("tool_calls")
    if tool_calls:
        item["tool_calls"] = tool_calls
    return item


def _run_tool(
    name: str,
    args: dict,
    *,
    document_id: str,
    user_id: str,
    path: str,
    search_budget: list[int],
    web_budget: list[int],
    mode: str = "document",
) -> str:
    try:
        if name in _PDF_TOOLS and mode != "document":
            return _tool_result({"error": "本轮不检索本地 PDF。", "hits": []})
        if name in _WEB_SEARCH_TOOLS:
            if web_budget[0] <= 0:
                return _tool_result({"error": "本轮外部搜索次数已用完。", "results": []})
            web_budget[0] -= 1
            query = str(args.get("query") or "").strip()
            num = int(args.get("num") or 5)
            topic = str(args.get("topic") or "").strip()
            eng = _web_search()
            if name == "baidu_web_search":
                return _tool_result(eng.baidu_web_search(query, num=num, topic=topic))
            if name == "baidu_baike_search":
                return _tool_result(eng.baidu_baike_search(query, num=num))
            return _tool_result(eng.google_web_search(query, num=num, topic=topic))

        doc = str(args.get("document_id") or document_id).strip() or document_id
        if doc != document_id:
            return _tool_result({"error": "只能检索当前打开的 PDF。"})
        if name == "ensure_office_pdf_index":
            return _tool_result(ensure_index(document_id=document_id, user_id=user_id, path=path))
        if name == "get_office_pdf_index_status":
            return _tool_result(get_index_status(document_id))
        if name == "search_office_pdf":
            if search_budget[0] <= 0:
                return _tool_result({"error": "本轮检索次数已用完。"})
            search_budget[0] -= 1
            query = str(args.get("query") or "").strip()
            top_k = int(args.get("top_k") or 8)
            hits = search_chunks(document_id=document_id, query=query, top_k=top_k)
            return _tool_result({"hits": hits, "count": len(hits)})
        return _tool_result({"error": f"未知工具：{name}"})
    except Exception as exc:
        return _tool_result({"error": str(exc), "results": []})


def _final_events(reasoning: str, content: str, model: str) -> list[dict]:
    thinking = (reasoning or "").strip()
    answer = compose_answer("", content)
    events: list[dict] = []
    if thinking:
        events.append({"type": "delta", "text": thinking, "part": "reasoning"})
    if answer:
        events.append({"type": "delta", "text": answer, "part": "answer"})
    events.append({"type": "done", "answer": answer, "thinking": thinking, "model_name": model})
    return events


async def _stream_composed_answer(messages: list[dict]) -> AsyncIterator[dict]:
    """reasoning 与正文分开流出，正文不包含思考过程。"""
    reasoning = ""
    content = ""
    async for event in iter_chat_events(messages, tools=None, stream=True, json_object=False, thinking=True):
        if event.get("type") != "delta" or not event.get("text"):
            continue
        part = event.get("part") or "answer"
        chunk = event["text"]
        if part == "reasoning":
            reasoning += chunk
            yield {"type": "delta", "text": chunk, "part": "reasoning"}
        else:
            content += chunk
            yield {"type": "delta", "text": chunk, "part": "answer"}
    answer = compose_answer("", content)
    yield {"type": "done", "answer": answer, "thinking": reasoning.strip(), "model_name": model_name() or "模型"}


async def iter_pdf_ask_events(
    *,
    selection: str,
    question: str,
    page: int,
    document_id: str,
    user_id: str,
    path: str,
    filename: str = "",
    history: list[dict] | None = None,
    ensure_first: bool = True,
) -> AsyncIterator[dict]:
    if not model_configured():
        raise ValueError("请先在后台配置模型（OFFICE_MODEL_BASE_URL、OFFICE_MODEL_NAME）。")
    text = (selection or "").strip()
    ask = (question or "").strip()
    doc_id = (document_id or "").strip()
    if not doc_id:
        raise ValueError("缺少 document_id。")
    if not text or len(text) > MAX_SELECTION:
        raise ValueError("请先选择要提问的文字。")
    if not ask or len(ask) > MAX_QUESTION:
        raise ValueError("问题需为 1 到 2000 个字符。")
    if not isinstance(page, int) or page < 1:
        raise ValueError("页码无效。")
    turns = _validate_history(history or [])
    name = model_name() or "模型"
    source = (filename or "PDF").strip() or "PDF"

    mode = ask_mode(ask)
    if ensure_first and mode == "document":
        try:
            status = ensure_index(document_id=doc_id, user_id=user_id, path=path)
            yield {"type": "index", "status": status.get("status"), "page_count": status.get("page_count"), "chunk_count": status.get("chunk_count")}
        except Exception as exc:
            yield {"type": "index", "status": "failed", "error": str(exc)}

    opener = f"文件：{source}\ndocument_id：{doc_id}\n焦点页：{page}\n提问类型：{mode}\n选区：\n{text}"
    first = ask if turns else f"{opener}\n\n问题：\n{ask}"
    messages: list[dict] = [{"role": "system", "content": _pdf_ask_system(mode)}]
    tools = _tools_for(mode)
    if turns:
        if turns[0]["role"] == "user":
            messages.append({"role": "user", "content": opener + "\n\n问题：\n" + turns[0]["content"]})
            messages.extend(turns[1:])
        else:
            messages.append({"role": "user", "content": opener})
            messages.extend(turns)
        messages.append({"role": "user", "content": first})
    else:
        messages.append({"role": "user", "content": first})

    search_budget = [MAX_SEARCH_PER_ROUND * MAX_TOOL_ROUNDS]
    web_budget = [MAX_WEB_PER_ROUND * MAX_TOOL_ROUNDS]
    for _ in range(MAX_TOOL_ROUNDS):
        message: dict | None = None
        async for event in iter_chat_events(messages, tools=tools, stream=False, tool_choice="auto", thinking=True):
            if event.get("type") == "message":
                message = event.get("message") or {}
        if not message:
            raise ValueError("模型未返回内容。")
        tool_calls = message.get("tool_calls") or []
        if not tool_calls:
            answer = compose_answer("", str(message.get("content") or ""))
            thinking = str(message.get("reasoning_content") or "").strip()
            if answer or thinking:
                for event in _final_events(thinking, str(message.get("content") or ""), name):
                    yield event
                return
            break
        messages.append(_assistant_message(message))
        round_pdf = 0
        round_web = 0
        for call in tool_calls:
            fn = (call.get("function") or {})
            tool_name = fn.get("name") or ""
            raw_args = fn.get("arguments") or "{}"
            try:
                args = json.loads(raw_args) if isinstance(raw_args, str) else (raw_args or {})
            except json.JSONDecodeError:
                args = {}
            if not isinstance(args, dict):
                args = {}
            if tool_name == "search_office_pdf":
                round_pdf += 1
                if round_pdf > MAX_SEARCH_PER_ROUND:
                    result = _tool_result({"error": "单轮 PDF 检索最多 2 次。"})
                else:
                    result = _run_tool(
                        tool_name, args, document_id=doc_id, user_id=user_id, path=path,
                        search_budget=search_budget, web_budget=web_budget, mode=mode,
                    )
            elif tool_name in _WEB_SEARCH_TOOLS:
                round_web += 1
                if round_web > MAX_WEB_PER_ROUND:
                    result = _tool_result({"error": "单轮外部搜索最多 3 次。", "results": []})
                else:
                    result = _run_tool(
                        tool_name, args, document_id=doc_id, user_id=user_id, path=path,
                        search_budget=search_budget, web_budget=web_budget, mode=mode,
                    )
            else:
                result = _run_tool(
                    tool_name, args, document_id=doc_id, user_id=user_id, path=path,
                    search_budget=search_budget, web_budget=web_budget, mode=mode,
                )
            messages.append({
                "role": "tool",
                "tool_call_id": call.get("id") or tool_name,
                "content": result,
            })

    messages.append({
        "role": "user",
        "content": (
            "请根据已得到的材料给出最终回答。"
            "没有内容的「本文搜索」「百度百科」「百度搜索」「Google搜索」不要写。"
            "只有这三个外部来源都没有内容时才写「模型搜索」，并用自身知识回答。"
            "最后始终写「总结」。使用简体中文 Markdown。"
        ),
    })
    async for event in _stream_composed_answer(messages):
        if event.get("type") == "done":
            event = {**event, "model_name": name}
        yield event
