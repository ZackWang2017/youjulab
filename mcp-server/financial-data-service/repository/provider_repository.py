"""数据提供者层。

现在：MockProvider 内置两个演示标的（DEMO_GROW / DEMO_WEAK），三期年报数据，今天即可跑通全流程。
未来：实现 RealApiProvider，接入你提供的准确财报 API。

切换：环境变量 DATA_PROVIDER=mock（默认）| real

报表字段（单位：亿元 CNY，年度口径）：
  利润表：revenue / cogs / net_profit / pretax_profit / three_fees / interest_expense
  资产负债表：equity / total_assets / total_liab / current_assets / current_liab /
             interest_debt / interest_debt_due_1y / productive_assets /
             accounts_receivable / notes_receivable / bank_acceptance / inventory /
             cash_balance / liquid_financial_assets
  现金流量表：ocf / cash_net_change
"""

from __future__ import annotations

import os

PERIODS = ["latest", "y1", "y2"]  # 最近年报、往前 1 年、往前 2 年


# ---------------------------------------------------------------------------
# Mock 数据（演示用，勿用于真实决策）
# ---------------------------------------------------------------------------
# DEMO_GROW：初筛大体通过（仅流动比/速动比偏高提示），分析评分 A 级
# DEMO_WEAK：应收占比>30% 一票排除 + 多项不达标，分析评分 D 级

MOCK_COMPANIES = {
    "DEMO_GROW": {
        "name": "演示成长股（Mock）",
        "industry": "消费电子（演示数据）",
        "business": "演示用虚构公司，不代表任何真实企业。",
        "currency": "CNY",
        "statements": {
            "latest": {
                "revenue": 120.0, "cogs": 70.0, "net_profit": 25.0, "pretax_profit": 36.0,
                "three_fees": 15.0, "interest_expense": 2.0,
                "equity": 160.0, "total_assets": 300.0, "total_liab": 120.0,
                "current_assets": 150.0, "current_liab": 60.0,
                "interest_debt": 28.0, "interest_debt_due_1y": 12.0, "productive_assets": 100.0,
                "accounts_receivable": 18.0, "notes_receivable": 8.0, "bank_acceptance": 6.0,
                "inventory": 18.0, "cash_balance": 40.0, "liquid_financial_assets": 5.0,
                "ocf": 26.0, "cash_net_change": 6.0,
            },
            "y1": {
                "revenue": 102.0, "cogs": 62.0, "net_profit": 20.0, "pretax_profit": 29.0,
                "three_fees": 13.0, "interest_expense": 2.2,
                "equity": 140.0, "total_assets": 270.0, "total_liab": 115.0,
                "current_assets": 130.0, "current_liab": 60.0,
                "interest_debt": 26.0, "interest_debt_due_1y": 12.0, "productive_assets": 82.0,
                "accounts_receivable": 15.0, "notes_receivable": 7.0, "bank_acceptance": 5.0,
                "inventory": 16.0, "cash_balance": 35.0, "liquid_financial_assets": 4.0,
                "ocf": 21.0, "cash_net_change": 5.0,
            },
            "y2": {
                "revenue": 88.0, "cogs": 55.0, "net_profit": 16.0, "pretax_profit": 23.0,
                "three_fees": 11.0, "interest_expense": 2.5,
                "equity": 122.0, "total_assets": 245.0, "total_liab": 110.0,
                "current_assets": 115.0, "current_liab": 58.0,
                "interest_debt": 24.0, "interest_debt_due_1y": 11.0, "productive_assets": 65.0,
                "accounts_receivable": 13.0, "notes_receivable": 6.0, "bank_acceptance": 4.5,
                "inventory": 14.0, "cash_balance": 30.0, "liquid_financial_assets": 3.0,
                "ocf": 15.0, "cash_net_change": 4.0,
            },
        },
    },
    "DEMO_WEAK": {
        "name": "演示弱势股（Mock）",
        "industry": "传统制造（演示数据）",
        "business": "演示用虚构公司，不代表任何真实企业。",
        "currency": "CNY",
        "statements": {
            "latest": {
                "revenue": 80.0, "cogs": 62.0, "net_profit": 4.0, "pretax_profit": 6.0,
                "three_fees": 20.0, "interest_expense": 5.0,
                "equity": 80.0, "total_assets": 320.0, "total_liab": 240.0,
                "current_assets": 134.0, "current_liab": 149.0,
                "interest_debt": 90.0, "interest_debt_due_1y": 40.0, "productive_assets": 150.0,
                "accounts_receivable": 95.0, "notes_receivable": 6.0, "bank_acceptance": 2.0,
                "inventory": 20.0, "cash_balance": 8.0, "liquid_financial_assets": 0.0,
                "ocf": 2.0, "cash_net_change": -3.0,
            },
            "y1": {
                "revenue": 83.0, "cogs": 62.0, "net_profit": 5.5, "pretax_profit": 8.0,
                "three_fees": 18.0, "interest_expense": 4.5,
                "equity": 92.0, "total_assets": 305.0, "total_liab": 213.0,
                "current_assets": 125.0, "current_liab": 120.0,
                "interest_debt": 82.0, "interest_debt_due_1y": 36.0, "productive_assets": 145.0,
                "accounts_receivable": 88.0, "notes_receivable": 6.0, "bank_acceptance": 2.0,
                "inventory": 18.0, "cash_balance": 10.0, "liquid_financial_assets": 0.0,
                "ocf": 4.0, "cash_net_change": -2.0,
            },
            "y2": {
                "revenue": 79.0, "cogs": 58.0, "net_profit": 6.0, "pretax_profit": 8.5,
                "three_fees": 15.0, "interest_expense": 4.0,
                "equity": 100.0, "total_assets": 280.0, "total_liab": 180.0,
                "current_assets": 118.0, "current_liab": 108.0,
                "interest_debt": 74.0, "interest_debt_due_1y": 32.0, "productive_assets": 138.0,
                "accounts_receivable": 82.0, "notes_receivable": 5.0, "bank_acceptance": 1.5,
                "inventory": 16.0, "cash_balance": 12.0, "liquid_financial_assets": 0.0,
                "ocf": 6.5, "cash_net_change": 1.0,
            },
        },
    },
}


class MockProvider:
    def list_tickers(self) -> list:
        return sorted(MOCK_COMPANIES)

    def get_company_info(self, ticker: str) -> dict:
        c = MOCK_COMPANIES[ticker]
        return {
            "ticker": ticker,
            "name": c["name"],
            "industry": c["industry"],
            "business": c["business"],
            "currency": c["currency"],
            "periods": PERIODS,
            "note": "MOCK 数据，仅供流程演示，不构成任何投资依据。",
        }

    def get_statements(self, ticker: str, period: str = "latest") -> dict:
        c = MOCK_COMPANIES[ticker]
        return {**c["statements"][period], "ticker": ticker, "period": period}


# ---------------------------------------------------------------------------
# 真实 API 提供者（TODO：接入你的准确财报 API）
# ---------------------------------------------------------------------------

class RealApiProvider:
    """接入你的财报数据 API 的占位实现。

    你需要：
    1. 设置环境变量 FINANCE_API_BASE_URL / FINANCE_API_KEY（或改 __init__ 默认值）
    2. 实现 _request(path, params)（你的 REST 客户端；鉴权方式按你的 API 而定）
    3. 实现三个接口，把返回字段对齐到本文件头注释的报表字段清单（亿元、年度口径）
    完成后设 DATA_PROVIDER=real 即可切换，mcp-server 其余代码不变。
    """

    def __init__(self):
        self.base_url = os.environ.get("FINANCE_API_BASE_URL", "https://your-api.example.com")
        self.api_key = os.environ.get("FINANCE_API_KEY", "")

    def _request(self, path: str, params: dict) -> dict:
        # TODO: requests / httpx 调用 self.base_url + path，带鉴权头
        raise NotImplementedError("实现你的 API 客户端后删除本行")

    def list_tickers(self) -> list:
        # TODO: 你的 API 若支持标的列表则实现；否则抛 NotImplementedError
        raise NotImplementedError("可选：标的列表")

    def get_company_info(self, ticker: str) -> dict:
        raise NotImplementedError("实现：公司基本信息（名称/行业/业务/币种）")

    def get_statements(self, ticker: str, period: str = "latest") -> dict:
        raise NotImplementedError("实现：财务报表（字段对齐文件头注释）")


def get_provider():
    name = os.environ.get("DATA_PROVIDER", "mock").lower()
    return RealApiProvider() if name == "real" else MockProvider()
