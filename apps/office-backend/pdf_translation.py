"""原生 PDF 的逐页英中对照翻译。仅发送当前页及极短相邻上下文。"""
from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import fitz

from assistant import iter_chat_events
from model_runtime import model_configuration_error, model_configured, model_name

MAX_PAGE_CHARS = 18_000
MAX_SELECTION_CHARS = 8_000
NEIGHBOR_CHARS = 1_600
SELECTION_CONTEXT_CHARS = 1_200

_DOMAIN_GUIDANCE = {
    "general": "通用商业材料：使用清晰、克制的商业中文。",
    "investment_research": "证券投研：准确区分业务、经营指标、估值、催化剂、风险、市场份额和货币单位。",
    "financial_report": "财报与公告：准确处理期间、同比/环比、收入、利润、现金流、指引、非 GAAP 与单位换算，绝不自行换算数字。",
    "accounting": "会计与审计：严格区分会计科目、确认/计量、递延、减值、合并范围、或有事项和审计意见。",
    "ipo_regulation": "IPO、交易与监管披露：准确处理发行、股权、承销、锁定期、风险因素、法律实体和监管术语。",
    "macro_policy": "宏观、行业与政策：准确处理统计口径、政策主体、经济指标、基点、税费与监管措辞。",
    "technology": "科技、软件与 AI：保留产品名、协议、技术标准、代码、模型名称及必要英文缩写。",
    "semiconductor": "半导体与硬件：准确处理制程、节点、晶圆、良率、封装、设备、出货、库存和规格单位。",
    "healthcare": "医疗健康与生物科技：准确处理适应症、临床阶段、终点、监管审批、试验数据和药物/器械名称。",
    "consumer_internet": "消费、互联网与平台经济：准确处理 GMV、MAU、渗透率、变现率、履约、用户增长和渠道术语。",
    "legal": "合同、法律与合规：保留权利义务、条件、例外、定义、期限和责任边界，不弱化限定语气。",
}

_MARKET_GUIDANCE = {
    "global": "按原文会计准则和披露口径翻译，不假定地区。",
    "china": "面向中国内地读者：保持中国会计、证券与监管常用译法；原文准则名称仍须保留。",
    "hong_kong": "面向港股/香港披露阅读：优先香港市场常用金融与公司治理译法，注意 HKFRS 及港交所语境。",
    "us": "面向美股/SEC 披露阅读：准确区分 US GAAP、SEC、Form 10-K/10-Q、ADR 等语境。",
    "europe": "面向欧洲/IFRS 披露阅读：准确处理 IFRS、欧盟监管与当地货币/单位。",
    "mixed": "跨市场材料：保留原始准则、监管机构和市场缩写，避免强行本地化。",
}

_MODE_GUIDANCE = {
    "faithful": "忠实对照：逐句贴近原文语序和限定条件，不为流畅而合并、拆分或改写事实。",
    "research": "投研阅读：在不改变任何事实、数字和限定语的前提下，使用自然、专业、可直接进入投研笔记的中文。",
    "terminology": "术语优先：关键专业术语首次出现采用“中文（English）”或保留必要英文，后续译法必须一致。",
}


def _page_text(document: fitz.Document, page_number: int) -> str:
    return document.load_page(page_number - 1).get_text("text").strip()


def _clip(text: str, length: int, *, tail: bool = False) -> str:
    text = text.strip()
    if len(text) <= length:
        return text
    return text[-length:] if tail else text[:length]


def _settings(value: dict[str, Any]) -> tuple[list[str], str, str, str]:
    raw = value.get("domains")
    domains = [str(item) for item in raw if str(item) in _DOMAIN_GUIDANCE] if isinstance(raw, list) else []
    if not domains:
        legacy = {"finance": "investment_research", "ipo": "ipo_regulation"}.get(str(value.get("domain") or ""), str(value.get("domain") or ""))
        domains = [legacy] if legacy in _DOMAIN_GUIDANCE else ["investment_research", "financial_report"]
    market = str(value.get("market") or "global")
    mode = str(value.get("mode") or "research")
    return domains[:4], market if market in _MARKET_GUIDANCE else "global", mode if mode in _MODE_GUIDANCE else "research", _clip(str(value.get("context") or ""), 2_000)


async def _iter_translation_events(
    source: str,
    before: str,
    after: str,
    settings: dict[str, Any],
    done: dict[str, Any],
) -> AsyncIterator[dict]:
    domains, market, mode, extra_context = _settings(settings)
    yield {"type": "progress", "message": "正在组织专业上下文…"}
    system = """你是专业英中翻译。只翻译“本页原文”，输出简体中文纯文本。
硬性规则：完整保留标题、段落、列表、脚注、表格行及其相对顺序；不得删减、概括、解释、补充事实或把相邻上下文翻译进结果。
事实保真：公司/产品/法律实体名、会计准则、股票代码、货币、数字、日期、百分比、基点、单位、负号、区间、比较期间、公式必须准确原样保留其含义；没有明确单位时不得猜测或换算。
术语一致：同一概念在本页使用同一译法；英文缩写、型号、标准、代码和专有名称按行业惯例保留。表格不得把列名与数值错配。
只输出译文正文，不输出 Markdown 标题、前言、注释、译者说明或“译文：”。"""
    user = (
        "专业场景：\n- " + "\n- ".join(_DOMAIN_GUIDANCE[item] for item in domains) + "\n"
        f"披露市场与准则语境：{_MARKET_GUIDANCE[market]}\n"
        f"译法取向：{_MODE_GUIDANCE[mode]}\n"
        f"用户补充背景：{extra_context or '无'}\n\n"
        f"上一页末尾（仅用于术语和指代判断，不翻译）：\n{before or '无'}\n\n"
        f"下一页开头（仅用于术语和指代判断，不翻译）：\n{after or '无'}\n\n"
        f"待翻译原文：\n{source}"
    )
    translated = ""
    yield {"type": "progress", "message": "正在进行专业翻译…"}
    async for event in iter_chat_events(
        [{"role": "system", "content": system}, {"role": "user", "content": user}],
        json_object=False,
        stream=True,
        thinking=True,
    ):
        if event.get("type") != "delta" or not event.get("text"):
            continue
        if event.get("part") == "reasoning":
            yield {"type": "thinking", "text": str(event["text"])}
        else:
            translated += str(event["text"])
    translated = translated.strip()
    if not translated:
        raise ValueError("模型没有返回译文。")
    yield {
        "type": "done",
        "source": source,
        "translation": translated,
        "domains": domains,
        "market": market,
        "mode": mode,
        "model_name": model_name() or "模型",
        **done,
    }


async def iter_pdf_page_translation_events(path: str, page_number: int, settings: dict[str, Any]) -> AsyncIterator[dict]:
    if not model_configured():
        raise ValueError(model_configuration_error())
    pdf_path = Path(path)
    if not pdf_path.is_file():
        raise ValueError("PDF 文件不存在。")
    yield {"type": "progress", "message": "正在提取本页原文…"}
    document = fitz.open(pdf_path)
    try:
        if page_number < 1 or page_number > document.page_count:
            raise ValueError(f"页码应为 1 至 {document.page_count}。")
        source = _page_text(document, page_number)
        if not source:
            raise ValueError("该页未提取到原生文本，暂不支持翻译扫描图片页。")
        if len(source) > MAX_PAGE_CHARS:
            raise ValueError(f"该页文本过长（最多 {MAX_PAGE_CHARS} 字），请先拆分 PDF 后再翻译。")
        before = _clip(_page_text(document, page_number - 1), NEIGHBOR_CHARS, tail=True) if page_number > 1 else ""
        after = _clip(_page_text(document, page_number + 1), NEIGHBOR_CHARS) if page_number < document.page_count else ""
    finally:
        document.close()

    async for event in _iter_translation_events(source, before, after, settings, {"page": page_number}):
        yield event


async def iter_pdf_selection_translation_events(
    path: str, page_number: int, selection: str, settings: dict[str, Any],
) -> AsyncIterator[dict]:
    if not model_configured():
        raise ValueError(model_configuration_error())
    source = selection.strip()
    if not source:
        raise ValueError("请先选中需要翻译的文本。")
    if len(source) > MAX_SELECTION_CHARS:
        raise ValueError(f"选中文本过长（最多 {MAX_SELECTION_CHARS} 字），请缩小选区后重试。")
    pdf_path = Path(path)
    if not pdf_path.is_file():
        raise ValueError("PDF 文件不存在。")
    yield {"type": "progress", "message": "正在提取选区上下文…"}
    document = fitz.open(pdf_path)
    try:
        if page_number < 1 or page_number > document.page_count:
            raise ValueError(f"页码应为 1 至 {document.page_count}。")
        page_text = _page_text(document, page_number)
        if not page_text:
            raise ValueError("该页未提取到原生文本，暂不支持翻译扫描图片页。")
        position = page_text.find(source)
        before = _clip(page_text[max(0, position - SELECTION_CONTEXT_CHARS):position], SELECTION_CONTEXT_CHARS, tail=True) if position >= 0 else ""
        after_start = position + len(source)
        after = _clip(page_text[after_start:after_start + SELECTION_CONTEXT_CHARS], SELECTION_CONTEXT_CHARS) if position >= 0 else ""
    finally:
        document.close()

    async for event in _iter_translation_events(source, before, after, settings, {"page": page_number}):
        yield event
