"""档案、报表与打分的编排。数字计算留在指标服务，取数留在 repository。"""
from __future__ import annotations

import profile_indicators_service as profile_indicators
import profile_repository
import provider_repository
import screening_service as screening

_provider = provider_repository.get_provider()


def get_profile() -> dict:
    """返回当前分析档案：指标清单、阈值、权重、评级线、报告模板。与 analysis-profile.yaml 同源。

    远程部署时分析档案只存在于服务端，本工具是模型获取口径的唯一可靠途径。
    """
    loaded = profile_repository.load_analysis_profile()
    return {
        "meta": loaded["meta"],
        "scoring": loaded["scoring"],
        "indicators": loaded["indicators"],
        "report_template": loaded["report_template"],
    }


def list_tickers() -> list:
    """列出当前数据提供者可分析的标的代码。"""
    return _provider.list_tickers()


def get_company_info(ticker: str) -> dict:
    """获取标的基本面信息（名称、行业、业务、币种、数据期间）。"""
    return _provider.get_company_info(ticker)


def get_statements(ticker: str, period: str = "latest") -> dict:
    """获取原始财务报表（period: latest / y1 / y2）。用于抽查明细与交叉验证，不要据此自行重算指标。"""
    return _provider.get_statements(ticker, period)


def get_indicators(ticker: str) -> dict:
    """核心工具：按分析档案确定性计算全部指标（三期序列、趋势、分维度得分、总分与 A/B/C/D 评级）。

    所有数字以此返回为准：模型不得自行估算或按原始报表重算。
    """
    loaded = profile_repository.load_analysis_profile()
    statements = {period: _provider.get_statements(ticker, period) for period in provider_repository.PERIODS}
    result = profile_indicators.compute(loaded, statements)
    result["company"] = _provider.get_company_info(ticker)
    result["periods"] = provider_repository.PERIODS
    return result


def screen_stock(ticker: str) -> dict:
    """初筛工具：按初筛指标档案的 12 项指标逐项确定性判定。

    模型不得自行重算指标；解读规则以本返回与 initial-screening skill 为准。
    """
    loaded = profile_repository.load_screening_profile()
    statements = {period: _provider.get_statements(ticker, period) for period in provider_repository.PERIODS}
    result = screening.compute(loaded, statements)
    result["company"] = _provider.get_company_info(ticker)
    return result
