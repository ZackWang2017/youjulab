"""调用后台配置的聊天模型（默认 DeepSeek 兼容接口），流式返回回答与可选补丁。"""
from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator

import httpx
import yaml

from config import ROOT, model_api_key, model_base_url, model_configured, model_name
from office_context import OfficeContext, selection_has_content
from office_patch import aim_sheet_write, create_patch

MAX_HISTORY = 20
MAX_TURN = 24000
MAX_SURROUNDING = 100_000
MAX_REQUEST_CHARS = 120_000

STREAM_HINT = {
    "explain": """
Stream a JSON object. Put the user-visible explanation in "answer".
Put an edit in "replacement" (null, a string, or a cell matrix matching the selection).
For sheet number-format-only edits, set replacement to null and put ranges in
"format_ops": [{"address":"C2:C20","number_format":"accounting"}].
Use number_format "accounting" (千分位两位小数) or "0.00%" (百分数保留两位).
Do not claim the edit has been applied.
""",
    "generate": """
Stream a JSON object. Put a short note in "answer".
For documents, replacement is Markdown (headings, bold, italic, lists, paragraphs)
that the editor converts to formatted rich text at the caret.
For sheets, replacement is a string or a cell matrix growing from the cursor.
Do not claim the insert has been applied.
""",
}

CHAT_HINT = """
Reply in Markdown. Do not wrap the explanation in JSON unless the user asked to edit cells
or change number formats.
Pure Q&A: write the final answer directly. Do not output chain-of-thought, 思考过程, or <think> blocks.
"""

MINDMAP_CHAT_HINT = """
Reply as one JSON object, even in a multi-turn conversation. Put a concise user-visible
message in "answer". When the user asks to alter the mind map, use
"mindmap_ops" and set "replacement" to null. Allowed operations are:
{"op":"add","parent_id":"existing node id","topic":"new node topic","index":0},
{"op":"update","id":"existing node id","topic":"new topic"},
{"op":"delete","id":"existing node id"}, and
{"op":"move","id":"existing node id","parent_id":"existing node id","index":0}.
Read node ids only from surrounding.tree. Never delete the root, move a node below
itself, invent an existing id, or claim that changes have already been applied.
For a question that needs no change, omit mindmap_ops and set replacement to null.
"""

ANALYSIS_INTENT = re.compile(r"分析|讨论|预测|推理|推测|研判|展望|解读|预判|预估|成因")
WRITE_INTENT = re.compile(r"写入|填写|填入|写到|写进|覆盖|替换单元格|修改单元格")
FORMAT_INTENT = re.compile(
    r"格式|数值类型|百分数|百分比|财务数值|千分位|number\s*format|改为.*(类型|格式)"
)


def answer_directive(intent: str) -> str:
    """指令、处理、统计只给结果；出现分析预测类字样才展开。写入/改格式必须给出可应用的 JSON。"""
    text = intent or ""
    if ANALYSIS_INTENT.search(text):
        return "回答要求：这是分析或预测。详细作答，分点说明依据、判断和不确定性。不要把思考过程写进正文。"
    if FORMAT_INTENT.search(text) and not WRITE_INTENT.search(text):
        return (
            "回答要求：这是修改单元格数字显示格式，不改单元格值。只输出一个 JSON 对象，不要写其它说明。"
            '格式：{"answer":"一句话说明改了哪些列","format_ops":'
            '[{"address":"C2:C20","number_format":"accounting"},'
            '{"address":"D2:E20","number_format":"0.00%"}],"replacement":null}。'
            "number_format 用 accounting（财务千分位两位小数）或 0.00%（百分数保留两位）。"
            "按表头区分金额列与环比/同比列；address 覆盖该列数据区（通常不含表头）。"
            "同一格式的相邻列务必合并成一个区域（如 B2:F100），format_ops 尽量只留少量条目，不要逐列拆开。"
            "不要声称已经改过表格。"
        )
    if WRITE_INTENT.search(text):
        return (
            "回答要求：这是写入单元格。只输出一个 JSON 对象，不要写其它说明。"
            '格式：{"answer":"一句话说明写入区域","address":"B13:I15","replacement":[[按行排列的值]]}。'
            "address 是要写入的 A1 区域，replacement 的行列数必须与该区域一致。"
            "金额用数值，文本保持字符串。不要声称已经写入表格。"
            "若同时改显示格式，可另加 format_ops："
            '[{"address":"C2:C20","number_format":"accounting"}]。'
        )
    return "回答要求：这是指令、数据处理或数据统计。只给出执行结果，不要写推理过程、分析过程或长篇说明。"

def _present_ask(ctx: OfficeContext, ask: str) -> str:
    if ctx.surface != "sheet":
        return ask
    return f"{ask}\n\n{answer_directive(ask)}"


THINK_FENCE = re.compile(r"<think>[\s\S]*?</think>", re.IGNORECASE)
THINK_OPEN = re.compile(r"<think>", re.IGNORECASE)
ANSWER_HEADING = re.compile(r"^##\s*回答\s*$", re.MULTILINE)
THINK_HEADING = re.compile(r"^##\s*思考过程\s*$", re.MULTILINE)


def strip_thinking(raw: str, *, keep_partial: bool = False) -> str:
    """去掉模型思考过程，只保留终答。keep_partial=True 时丢弃尚未闭合的 <think>。"""
    text = THINK_FENCE.sub("", raw or "")
    opened = THINK_OPEN.search(text)
    if opened and not re.search(r"</think>", text[opened.start():], re.IGNORECASE):
        text = text[:opened.start()] if not keep_partial else text
    think = THINK_HEADING.search(text)
    answer = ANSWER_HEADING.search(text)
    if think and answer and think.start() < answer.start():
        text = text[answer.end():].lstrip("\n")
    elif think and not answer:
        text = text[:think.start()]
    return text.strip() if not keep_partial else text


def clip_turn_content(content: str, limit: int = MAX_TURN) -> str:
    text = strip_thinking(content).strip()
    if len(text) > limit:
        return text[:limit]
    return text


def _validate_history(history: list[dict]) -> list[dict]:
    if not isinstance(history, list):
        raise ValueError("问答历史格式无效。")
    recent = history[-MAX_HISTORY:] if len(history) > MAX_HISTORY else history
    cleaned: list[dict] = []
    for item in recent:
        if not isinstance(item, dict):
            raise ValueError("问答历史格式无效。")
        role = item.get("role")
        content = clip_turn_content(item.get("content") or "")
        if role not in ("user", "assistant"):
            raise ValueError("问答历史角色无效。")
        if not content:
            raise ValueError("问答历史内容无效。")
        cleaned.append({"role": role, "content": content})
    return cleaned


def _context_has_task_content(ctx: OfficeContext, history: list[dict]) -> bool:
    if selection_has_content(ctx):
        return True
    if (ctx.surrounding or "").strip():
        return True
    return bool(history)


def _build_opener(ctx: OfficeContext, summary: str = "") -> str:
    sel = ctx.selection
    parts = [
        f"document_id：{ctx.document_id}",
        f"surface：{ctx.surface}",
        f"revision：{ctx.revision}",
    ]
    if ctx.surface == "sheet":
        parts.append(f"sheet_id：{sel.get('sheet_id', '')}")
        address = sel.get("address")
        if isinstance(address, str) and address.strip():
            parts.append(f"选区：{address.strip()}")
    summary = (summary or "").strip()
    if summary:
        parts.append(f"较早对话摘要：\n{summary}")
    surrounding = (ctx.surrounding or "").strip()
    if surrounding:
        parts.append(f"surrounding：\n{surrounding}")
    return "\n".join(parts)


def _uses_chat_messages(ctx: OfficeContext, history: list[dict], task: str = "explain") -> bool:
    if ctx.surface == "mindmap":
        return True
    if ctx.surface == "sheet" and task == "explain":
        return True
    if history:
        return True
    return ctx.surface == "sheet" and bool((ctx.surrounding or "").strip())


def _truncate_surrounding_json(raw: str, max_len: int = MAX_SURROUNDING) -> tuple[str, bool]:
    text = (raw or "").strip()
    if not text or len(text) <= max_len:
        return text, False
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return text[:max_len], True
    if not isinstance(payload, dict):
        return text[:max_len], True
    if isinstance(payload.get("tree"), dict):
        copy = {**payload, "tree": None, "truncated": True}
        queue = [payload["tree"]]
        kept: list[dict] = []
        while queue:
            node = queue.pop(0)
            if not isinstance(node, dict):
                continue
            candidate = {key: value for key, value in node.items() if key != "children"}
            candidate["children"] = []
            trial = {**copy, "tree": {"id": payload["tree"].get("id", "root"), "topic": payload["tree"].get("topic", ""), "children": kept + [candidate]}}
            if len(json.dumps(trial, ensure_ascii=False)) > max_len:
                break
            kept.append(candidate)
            queue.extend(node.get("children") or [])
        copy["tree"] = {"id": payload["tree"].get("id", "root"), "topic": payload["tree"].get("topic", ""), "children": kept}
        return json.dumps(copy, ensure_ascii=False), True
    values = payload.get("values")
    if not isinstance(values, list):
        payload["truncated"] = True
        out = json.dumps(payload, ensure_ascii=False)
        return (out[:max_len], True) if len(out) > max_len else (out, True)
    copy = {**payload, "values": [], "truncated": True}
    for row in values:
        if not isinstance(row, list):
            continue
        copy["values"].append(row)
        copy["rows"] = len(copy["values"])
        copy["cols"] = len(copy["values"][0]) if copy["values"] else 0
        candidate = json.dumps(copy, ensure_ascii=False)
        if len(candidate) > max_len:
            copy["values"].pop()
            copy["rows"] = len(copy["values"])
            copy["cols"] = len(copy["values"][0]) if copy["values"] else 0
            break
    out = json.dumps(copy, ensure_ascii=False)
    return out, True


def _message_size(ctx: OfficeContext, history: list[dict], intent: str, summary: str = "") -> int:
    opener = _build_opener(ctx, summary)
    total = len(opener) + len(intent.strip()) + 2
    for item in history:
        total += len(item.get("content") or "") + 16
    return total


def _fit_chat_budget(
    ctx: OfficeContext,
    history: list[dict],
    intent: str,
    *,
    summary: str = "",
) -> tuple[OfficeContext, list[dict], dict]:
    meta = {"dropped_turns": 0, "surrounding_truncated": False}
    surrounding, truncated = _truncate_surrounding_json(ctx.surrounding or "")
    meta["surrounding_truncated"] = truncated
    fitted = ctx.model_copy(update={"surrounding": surrounding})
    kept: list[dict] = []
    dropped = 0
    for item in reversed(history):
        trial = [item, *kept]
        if _message_size(fitted, trial, intent, summary) <= MAX_REQUEST_CHARS:
            kept = trial
        else:
            dropped += 1
    meta["dropped_turns"] = dropped
    if dropped and not summary.strip():
        note = f"较早 {dropped} 轮对话已省略以适配模型上下文。"
        fitted = fitted.model_copy(update={"surrounding": f"{note}\n{fitted.surrounding}".strip()})
    return fitted, kept, meta


def registry(surface: str, task: str = "explain", *, chat_mode: bool = False) -> tuple[str, str]:
    if task not in STREAM_HINT:
        raise ValueError("未知任务。")
    router = yaml.safe_load((ROOT / "agents/office-copilot.agent.yaml").read_text())
    routes = router["routes"][surface]
    if isinstance(routes, dict):
        if task not in routes:
            raise ValueError("PDF 仅支持选区解释。" if surface == "pdf" else "当前类型不支持该任务。")
        filename = routes[task]
    else:
        filename = routes
    agent = yaml.safe_load((ROOT / "agents" / filename).read_text())
    skill = (ROOT / "skills" / agent["skill"] / "SKILL.md").read_text()
    contract = (ROOT / "skills/common/contracts.md").read_text()
    parts = [router["instructions"], skill]
    # 表格选区解释：内联特征描述 skill（均值/中位/环比/趋势）
    if surface == "sheet" and task == "explain":
        features = ROOT / "skills/spreadsheet-features/SKILL.md"
        if features.is_file():
            parts.append(features.read_text())
        if chat_mode:
            chat = ROOT / "skills/spreadsheet-chat/SKILL.md"
            if chat.is_file():
                parts.append(chat.read_text())
    parts.append(contract)
    if chat_mode:
        parts.append(MINDMAP_CHAT_HINT if surface == "mindmap" else CHAT_HINT)
    else:
        parts.append(STREAM_HINT[task])
    return agent["name"], "\n".join(parts)


def visible_json_string(raw: str, field: str = "answer") -> str:
    """从尚未闭合的 JSON 对象里抽出字段当前已出现的字符串内容，供流式展示。"""
    needle = f'"{field}"'
    start = raw.find(needle)
    if start < 0:
        return ""
    rest = raw[start + len(needle):]
    i = 0
    while i < len(rest) and rest[i] in " \n\r\t:":
        i += 1
    if i >= len(rest) or rest[i] != '"':
        return ""
    i += 1
    out: list[str] = []
    while i < len(rest):
        ch = rest[i]
        if ch == "\\":
            if i + 1 >= len(rest):
                break
            nxt = rest[i + 1]
            if nxt == "u":
                hex_digits = rest[i + 2:i + 6]
                if len(hex_digits) < 4:
                    break
                try:
                    out.append(chr(int(hex_digits, 16)))
                except ValueError:
                    break
                i += 6
                continue
            out.append({"n": "\n", "t": "\t", "r": "\r", '"': '"', "\\": "\\", "/": "/"}.get(nxt, nxt))
            i += 2
            continue
        if ch == '"':
            break
        out.append(ch)
        i += 1
    return "".join(out)


async def stream_model_tokens(messages: list[dict], json_object: bool = True) -> AsyncIterator[str | dict]:
    """问答模式开启思考：reasoning 以 dict 单独流出，终答仍是字符串。"""
    thinking = not json_object
    async for event in iter_chat_events(
        messages, json_object=json_object, tools=None, stream=True, thinking=thinking,
    ):
        if event.get("type") != "delta" or not event.get("text"):
            continue
        if event.get("part") == "reasoning":
            yield {"part": "reasoning", "text": event["text"]}
            continue
        yield event["text"]


async def iter_chat_events(
    messages: list[dict],
    *,
    json_object: bool = False,
    tools: list[dict] | None = None,
    stream: bool = True,
    tool_choice: str | dict | None = None,
    thinking: bool = False,
) -> AsyncIterator[dict]:
    """调用聊天接口。stream=True 时产出 delta；含 tools 且非流式时产出 message（可能带 tool_calls）。

    thinking=True 时启用 DeepSeek 思考模式：reasoning_content 与 content 都会产出。
    """
    base = model_base_url()
    name = model_name()
    if not base or not name:
        raise ValueError("请先在后台配置 OFFICE_MODEL_BASE_URL 和 OFFICE_MODEL_NAME。")
    headers = {"Content-Type": "application/json"}
    key = model_api_key()
    if key:
        headers["Authorization"] = f"Bearer {key}"
    payload: dict = {
        "model": name,
        "messages": messages,
        "stream": stream,
    }
    if thinking:
        payload["thinking"] = {"type": "enabled"}
        payload["reasoning_effort"] = "high"
    if tools:
        payload["tools"] = tools
        if tool_choice is not None:
            payload["tool_choice"] = tool_choice
    elif json_object:
        payload["response_format"] = {"type": "json_object"}
    timeout = httpx.Timeout(15.0, read=180.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        if not stream:
            response = await client.post(f"{base}/chat/completions", headers=headers, json=payload)
            if response.is_error:
                detail = response.text[:400]
                raise ValueError(f"模型接口返回 HTTP {response.status_code}。{detail}")
            data = response.json()
            message = ((data.get("choices") or [{}])[0].get("message") or {})
            yield {"type": "message", "message": message}
            return
        async with client.stream("POST", f"{base}/chat/completions", headers=headers, json=payload) as response:
            if response.is_error:
                detail = (await response.aread()).decode("utf-8", "replace")[:400]
                raise ValueError(f"模型接口返回 HTTP {response.status_code}。{detail}")
            async for line in response.aiter_lines():
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if not data or data == "[DONE]":
                    if data == "[DONE]":
                        break
                    continue
                try:
                    chunk = json.loads(data)
                except json.JSONDecodeError:
                    continue
                delta = ((chunk.get("choices") or [{}])[0].get("delta") or {})
                reasoning = delta.get("reasoning_content") or delta.get("reasoning")
                if reasoning:
                    yield {"type": "delta", "text": reasoning, "part": "reasoning"}
                text = delta.get("content")
                if text:
                    yield {"type": "delta", "text": text, "part": "answer"}


def _strip_code_fence(raw: str) -> str:
    text = (raw or "").strip()
    if not text.startswith("```"):
        return text
    body = text.split("\n", 1)[-1]
    if body.endswith("```"):
        body = body[:-3]
    body = body.strip()
    if body[:4].lower() == "json":
        body = body[4:].lstrip()
    return body.strip()


def parse_model_result(raw: str, *, fallback: str = "", require_json: bool = True) -> dict:
    text = _strip_code_fence(raw)
    candidates = [text]
    start, end = text.find("{"), text.rfind("}")
    if 0 <= start < end:
        candidates.append(text[start:end + 1])
    for candidate in candidates:
        try:
            result = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(result, dict) and isinstance(result.get("answer"), str):
            result["answer"] = strip_thinking(result["answer"])
            return result
    answer = visible_json_string(raw) or fallback.strip() or text
    if answer.strip():
        return {"answer": strip_thinking(answer), "replacement": None}
    if require_json:
        raise ValueError("模型没有返回可解析的 JSON。")
    raise ValueError("模型没有返回内容。")


def _assistant_messages(
    ctx: OfficeContext,
    intent: str,
    task: str,
    history: list[dict],
    *,
    system: str,
    summary: str = "",
) -> list[dict]:
    messages: list[dict] = [{"role": "system", "content": system}]
    ask = _present_ask(ctx, intent.strip())
    if _uses_chat_messages(ctx, history, task):
        opener = _build_opener(ctx, summary)
        if history:
            if history[0]["role"] == "user":
                messages.append({"role": "user", "content": f"{opener}\n\n问题：\n{history[0]['content']}"})
                messages.extend(history[1:])
            else:
                messages.append({"role": "user", "content": opener})
                messages.extend(history)
            messages.append({"role": "user", "content": ask})
        else:
            messages.append({"role": "user", "content": f"{opener}\n\n问题：\n{ask}"})
        return messages
    messages.append({
        "role": "user",
        "content": json.dumps({"task": task, "intent": ask, "context": ctx.model_dump()}, ensure_ascii=False),
    })
    return messages


async def iter_assistant_events(
    context: dict,
    intent: str,
    task: str = "explain",
    history: list[dict] | None = None,
    summary: str = "",
) -> AsyncIterator[dict]:
    if not intent.strip() or len(intent) > 4000:
        raise ValueError("任务需为 1 到 4000 个字符。")
    if not model_configured():
        raise ValueError("请先在后台配置模型（OFFICE_MODEL_BASE_URL、OFFICE_MODEL_NAME）。")
    if task not in STREAM_HINT:
        raise ValueError("未知任务。")
    ctx = OfficeContext.model_validate(context)
    turns = _validate_history(history or [])
    if task == "explain" and not _context_has_task_content(ctx, turns):
        raise ValueError("未选择任务内容。")
    trim_meta = {"dropped_turns": 0, "surrounding_truncated": False}
    session_summary = (summary or "").strip()
    if _uses_chat_messages(ctx, turns, task):
        ctx, turns, trim_meta = _fit_chat_budget(ctx, turns, intent, summary=session_summary)
    chat_mode = _uses_chat_messages(ctx, turns, task)
    agent, system = registry(ctx.surface, task, chat_mode=chat_mode)
    messages = _assistant_messages(ctx, intent, task, turns, system=system, summary=session_summary)
    raw = ""
    shown = ""
    reasoning_sent = 0
    json_mode = not chat_mode
    async for token in stream_model_tokens(messages, json_object=json_mode):
        if isinstance(token, dict) and token.get("part") == "reasoning":
            text = str(token.get("text") or "")
            if reasoning_sent >= 12000 or not text:
                continue
            chunk = text[:12000 - reasoning_sent]
            reasoning_sent += len(chunk)
            yield {"type": "delta", "text": chunk, "part": "reasoning"}
            continue
        raw += token
        cleaned = strip_thinking(raw)
        stripped = cleaned.lstrip()
        json_like = json_mode or stripped.startswith("{") or stripped.startswith("```")
        extracted = visible_json_string(cleaned)
        if extracted:
            visible = extracted
        elif json_like:
            visible = shown
        else:
            visible = cleaned
        if len(visible) > len(shown):
            yield {"type": "delta", "text": visible[len(shown):]}
            shown = visible
    result = parse_model_result(raw, fallback=shown, require_json=json_mode or ctx.surface == "mindmap")
    if result["answer"] != shown:
        extra = result["answer"][len(shown):] if result["answer"].startswith(shown) else result["answer"]
        if extra:
            yield {"type": "delta", "text": extra}
    patch = None
    has_values = result.get("replacement") is not None
    has_formats = bool(result.get("format_ops"))
    has_mindmap_ops = bool(result.get("mindmap_ops"))
    if has_values or has_formats or has_mindmap_ops:
        try:
            target = aim_sheet_write(ctx, result) if ctx.surface == "sheet" and has_values else ctx
            patch = create_patch(
                target,
                result.get("replacement"),
                task,
                format_ops=result.get("format_ops") if has_formats else None,
                mindmap_ops=result.get("mindmap_ops") if has_mindmap_ops else None,
            )
        except ValueError as exc:
            patch = None
            note = f"\n未能生成可应用的修改：{exc}"
            if note.strip() not in result["answer"]:
                yield {"type": "delta", "text": note}
                result["answer"] += note
    done: dict = {"type": "done", "agent": agent, "answer": result["answer"], "patch": patch}
    if trim_meta.get("dropped_turns") or trim_meta.get("surrounding_truncated"):
        done["context_trimmed"] = trim_meta
    yield done


def sse_pack(event: dict) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
