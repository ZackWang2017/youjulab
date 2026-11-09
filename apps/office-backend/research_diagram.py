"""Validate the research diagram envelope and financial account references."""
from __future__ import annotations


def research_content(content: dict) -> dict:
    if content.get("format") != "invp-research-diagram-v1" or content.get("type") not in {"timeline", "fishbone", "financial_accounts"}:
        raise ValueError("研究图格式或图类型无效。")
    if content["type"] != "financial_accounts":
        return content
    stock = content.get("stock")
    if not isinstance(stock, dict) or not str(stock.get("ticker_code") or "").strip() or not str(stock.get("name") or "").strip():
        raise ValueError("财务账户分析必须选择股票。")
    if content.get("standard") not in {"CAS", "IFRS", "HKFRS", "US_GAAP"}:
        raise ValueError("请选择有效的会计准则。")
    accounts, rules = content.get("accounts"), content.get("rules")
    if not isinstance(accounts, list) or not isinstance(rules, list) or len(accounts) > 500 or len(rules) > 2000:
        raise ValueError("账户或记账规则格式无效或数量过多。")
    ids: set[str] = set()
    for account in accounts:
        if not isinstance(account, dict) or not isinstance(account.get("id"), str) or not account["id"] or account["id"] in ids:
            raise ValueError("账户 ID 缺失或重复。")
        if account.get("group") not in {"asset", "liability", "equity", "income", "expense"} or account.get("normal") not in {"debit", "credit"}:
            raise ValueError("账户分类或余额方向无效。")
        ids.add(account["id"])
    rule_ids: set[str] = set()
    for rule in rules:
        if not isinstance(rule, dict) or not isinstance(rule.get("id"), str) or not rule["id"] or rule["id"] in rule_ids:
            raise ValueError("记账规则 ID 缺失或重复。")
        rule_ids.add(rule["id"])
        for side in ("debits", "credits"):
            refs = rule.get(side)
            if not isinstance(refs, list) or any(not isinstance(ref, str) or ref not in ids for ref in refs):
                raise ValueError("记账规则引用了不存在的账户。")
    for rule in rules:
        following = rule.get("next", [])
        if not isinstance(following, list) or any(not isinstance(ref, str) or ref not in rule_ids or ref == rule["id"] for ref in following):
            raise ValueError("后续记账规则引用无效。")
    return content
