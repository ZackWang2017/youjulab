"""抓取 A 股三大报表，写入 financial_statements（与港股同一张表、同一套字段）。

优先走东方财富 datacenter，复用 eastmoney_repository 的客户端、记录结构和入库。
A 股接口是「一报告期一行、科目为列」的宽表，这里拆成与港股相同的长表；
列名能对应到会计准则科目的写成中文，对不上的保留英文列名，避免丢数。
东方财富请求失败或返回空时，改从新浪财经的财报 JSON 抓取（科目本身就是中文）。

用法（在 mcp-server 目录下）：
  ../.venv/bin/python financial-data-service/repository/a_share_statement_repository.py --ticker 600519.SH
  ../.venv/bin/python financial-data-service/repository/a_share_statement_repository.py --ticker 000596 --source sina
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import eastmoney_repository as em  # noqa: E402

log = logging.getLogger("financial.a_share")

A_SHARE_MARKETS = {"SH", "SZ", "BJ"}
DATA_SOURCE_EM = "东方财富"
DATA_SOURCE_SINA = "新浪财经"

# 东方财富 A 股宽表。与港股的 RPT_HKF10_* 不同，这里一报告期一行。
A_SHARE_REPORTS: dict[str, str] = {
    "资产负债表": "RPT_F10_FINANCE_GBALANCE",
    "利润表": "RPT_F10_FINANCE_GINCOME",
    "现金流量表": "RPT_F10_FINANCE_GCASHFLOW",
}

EM_PERIOD = {"年报": "年报", "中报": "2季报", "半年报": "2季报", "一季报": "1季报", "三季报": "3季报", "四季报": "4季报"}
EM_SKIP_SUFFIXES = ("_YOY", "_RATIO")
EM_META_FIELDS = {
    "SECUCODE", "SECURITY_CODE", "SECURITY_NAME_ABBR", "ORG_CODE", "ORG_TYPE",
    "REPORT_DATE", "REPORT_TYPE", "REPORT_DATE_NAME", "SECURITY_TYPE_CODE",
    "NOTICE_DATE", "UPDATE_DATE", "CURRENCY", "LISTING_STATE", "OPINION_TYPE",
    "OSOPINION_TYPE", "AUDIT_OPINION",
}

SINA_URL = "https://quotes.sina.cn/cn/api/openapi.php/CompanyFinanceService.getFinanceReport2022"
SINA_SOURCES = {"资产负债表": "fzb", "利润表": "lrb", "现金流量表": "llb"}
SINA_PERIOD = {1: "1季报", 2: "2季报", 3: "3季报", 4: "年报"}
SINA_PAGE_SIZE = 100

# 东方财富英文字段 → 报表科目。未收录的字段按原列名入库。
A_SHARE_ITEM_NAMES: dict[str, str] = {
    "MONETARYFUNDS": "货币资金",
    "SETTLE_EXCESS_RESERVE": "结算备付金",
    "LEND_FUND": "拆出资金",
    "TRADE_FINASSET": "交易性金融资产",
    "TRADE_FINASSET_NOTFVTPL": "交易性金融资产（非以公允价值计量）",
    "FVTPL_FINASSET": "以公允价值计量且其变动计入当期损益的金融资产",
    "APPOINT_FVTPL_FINASSET": "指定为以公允价值计量且其变动计入当期损益的金融资产",
    "DERIVE_FINASSET": "衍生金融资产",
    "NOTE_RECE": "应收票据",
    "ACCOUNTS_RECE": "应收账款",
    "NOTE_ACCOUNTS_RECE": "应收票据及应收账款",
    "FINANCE_RECE": "应收款项融资",
    "PREPAYMENT": "预付款项",
    "PREMIUM_RECE": "应收保费",
    "REINSURE_RECE": "应收分保账款",
    "RC_RESERVE_RECE": "应收分保合同准备金",
    "OTHER_RECE": "其他应收款",
    "TOTAL_OTHER_RECE": "其他应收款合计",
    "DIVIDEND_RECE": "应收股利",
    "INTEREST_RECE": "应收利息",
    "EXPORT_REFUND_RECE": "应收出口退税",
    "SUBSIDY_RECE": "应收补贴款",
    "INTERNAL_RECE": "内部应收款",
    "INVENTORY": "存货",
    "CONTRACT_ASSET": "合同资产",
    "HOLDSALE_ASSET": "持有待售资产",
    "DIV_HOLDSALE_ASSET": "划分为持有待售的资产",
    "NONCURRENT_ASSET_1YEAR": "一年内到期的非流动资产",
    "OTHER_CURRENT_ASSET": "其他流动资产",
    "CURRENT_ASSET_OTHER": "流动资产其他项目",
    "CURRENT_ASSET_BALANCE": "流动资产平衡项目",
    "TOTAL_CURRENT_ASSETS": "流动资产合计",
    "LOAN_ADVANCE": "发放贷款及垫款",
    "CREDITOR_INVEST": "债权投资",
    "OTHER_CREDITOR_INVEST": "其他债权投资",
    "AMORTIZE_COST_FINASSET": "以摊余成本计量的金融资产",
    "AMORTIZE_COST_NCFINASSET": "以摊余成本计量的非流动金融资产",
    "FVTOCI_FINASSET": "以公允价值计量且其变动计入其他综合收益的金融资产",
    "FVTOCI_NCFINASSET": "以公允价值计量且其变动计入其他综合收益的非流动金融资产",
    "AVAILABLE_SALE_FINASSET": "可供出售金融资产",
    "HOLD_MATURITY_INVEST": "持有至到期投资",
    "LONG_RECE": "长期应收款",
    "LONG_EQUITY_INVEST": "长期股权投资",
    "OTHER_EQUITY_INVEST": "其他权益工具投资",
    "OTHER_NONCURRENT_FINASSET": "其他非流动金融资产",
    "INVEST_REALESTATE": "投资性房地产",
    "FIXED_ASSET": "固定资产",
    "FIXED_ASSET_DISPOSAL": "固定资产清理",
    "CIP": "在建工程",
    "PROJECT_MATERIAL": "工程物资",
    "PRODUCTIVE_BIOLOGY_ASSET": "生产性生物资产",
    "CONSUMPTIVE_BIOLOGICAL_ASSET": "消耗性生物资产",
    "OIL_GAS_ASSET": "油气资产",
    "USERIGHT_ASSET": "使用权资产",
    "INTANGIBLE_ASSET": "无形资产",
    "DEVELOP_EXPENSE": "开发支出",
    "GOODWILL": "商誉",
    "LONG_PREPAID_EXPENSE": "长期待摊费用",
    "DEFER_TAX_ASSET": "递延所得税资产",
    "OTHER_NONCURRENT_ASSET": "其他非流动资产",
    "NONCURRENT_ASSET_OTHER": "非流动资产其他项目",
    "NONCURRENT_ASSET_BALANCE": "非流动资产平衡项目",
    "TOTAL_NONCURRENT_ASSETS": "非流动资产合计",
    "ASSET_OTHER": "资产其他项目",
    "ASSET_BALANCE": "资产平衡项目",
    "TOTAL_ASSETS": "资产总计",
    "SHORT_LOAN": "短期借款",
    "BORROW_FUND": "拆入资金",
    "TRADE_FINLIAB": "交易性金融负债",
    "TRADE_FINLIAB_NOTFVTPL": "交易性金融负债（非以公允价值计量）",
    "FVTPL_FINLIAB": "以公允价值计量且其变动计入当期损益的金融负债",
    "APPOINT_FVTPL_FINLIAB": "指定为以公允价值计量且其变动计入当期损益的金融负债",
    "DERIVE_FINLIAB": "衍生金融负债",
    "NOTE_PAYABLE": "应付票据",
    "ACCOUNTS_PAYABLE": "应付账款",
    "NOTE_ACCOUNTS_PAYABLE": "应付票据及应付账款",
    "ADVANCE_RECEIVABLES": "预收款项",
    "CONTRACT_LIAB": "合同负债",
    "SELL_REPO_FINASSET": "卖出回购金融资产款",
    "ACCEPT_DEPOSIT_INTERBANK": "吸收存款及同业存放",
    "AGENT_TRADE_SECURITY": "代理买卖证券款",
    "AGENT_UNDERWRITE_SECURITY": "代理承销证券款",
    "STAFF_SALARY_PAYABLE": "应付职工薪酬",
    "TAX_PAYABLE": "应交税费",
    "OTHER_PAYABLE": "其他应付款",
    "TOTAL_OTHER_PAYABLE": "其他应付款合计",
    "INTEREST_PAYABLE": "应付利息",
    "DIVIDEND_PAYABLE": "应付股利",
    "FEE_COMMISSION_PAYABLE": "应付手续费及佣金",
    "REINSURE_PAYABLE": "应付分保账款",
    "INTERNAL_PAYABLE": "内部应付款",
    "HOLDSALE_LIAB": "持有待售负债",
    "DIV_HOLDSALE_LIAB": "划分为持有待售的负债",
    "NONCURRENT_LIAB_1YEAR": "一年内到期的非流动负债",
    "SHORT_BOND_PAYABLE": "应付短期债券",
    "SHORT_FIN_PAYABLE": "短期应付债券",
    "ACCRUED_EXPENSE": "预提费用",
    "PREDICT_CURRENT_LIAB": "预计流动负债",
    "OTHER_CURRENT_LIAB": "其他流动负债",
    "CURRENT_LIAB_OTHER": "流动负债其他项目",
    "CURRENT_LIAB_BALANCE": "流动负债平衡项目",
    "TOTAL_CURRENT_LIAB": "流动负债合计",
    "LONG_LOAN": "长期借款",
    "BOND_PAYABLE": "应付债券",
    "PREFERRED_SHARES_PAYBALE": "应付债券：优先股",
    "PERPETUAL_BOND_PAYBALE": "应付债券：永续债",
    "LEASE_LIAB": "租赁负债",
    "LONG_PAYABLE": "长期应付款",
    "LONG_STAFFSALARY_PAYABLE": "长期应付职工薪酬",
    "SPECIAL_PAYABLE": "专项应付款",
    "PREDICT_LIAB": "预计负债",
    "DEFER_INCOME": "递延收益",
    "DEFER_INCOME_1YEAR": "一年内到期的递延收益",
    "DEFER_TAX_LIAB": "递延所得税负债",
    "OTHER_NONCURRENT_LIAB": "其他非流动负债",
    "NONCURRENT_LIAB_OTHER": "非流动负债其他项目",
    "NONCURRENT_LIAB_BALANCE": "非流动负债平衡项目",
    "TOTAL_NONCURRENT_LIAB": "非流动负债合计",
    "LIAB_OTHER": "负债其他项目",
    "LIAB_BALANCE": "负债平衡项目",
    "TOTAL_LIABILITIES": "负债合计",
    "SHARE_CAPITAL": "实收资本（或股本）",
    "OTHER_EQUITY_TOOL": "其他权益工具",
    "PREFERRED_SHARES": "其他权益工具：优先股",
    "PERPETUAL_BOND": "其他权益工具：永续债",
    "CAPITAL_RESERVE": "资本公积",
    "TREASURY_SHARES": "库存股",
    "OTHER_COMPRE_INCOME": "其他综合收益",
    "SPECIAL_RESERVE": "专项储备",
    "SURPLUS_RESERVE": "盈余公积",
    "GENERAL_RISK_RESERVE": "一般风险准备",
    "UNASSIGN_RPOFIT": "未分配利润",
    "ASSIGN_CASH_DIVIDEND": "拟分配现金股利",
    "PARENT_EQUITY_OTHER": "归属于母公司股东权益其他项目",
    "PARENT_EQUITY_BALANCE": "归属于母公司股东权益平衡项目",
    "TOTAL_PARENT_EQUITY": "归属于母公司股东权益合计",
    "MINORITY_EQUITY": "少数股东权益",
    "EQUITY_OTHER": "股东权益其他项目",
    "EQUITY_BALANCE": "股东权益平衡项目",
    "TOTAL_EQUITY": "股东权益合计",
    "LIAB_EQUITY_OTHER": "负债和股东权益其他项目",
    "LIAB_EQUITY_BALANCE": "负债和股东权益平衡项目",
    "TOTAL_LIAB_EQUITY": "负债和股东权益总计",
    "OTHER_EQUITY_OTHER": "其他权益其他项目",
    "LOAN_PBC": "向中央银行借款",
    "BUY_RESALE_FINASSET": "买入返售金融资产",
    "FIN_FUND": "融出资金",
    "AMORTIZE_COST_FINLIAB": "以摊余成本计量的金融负债",
    "AMORTIZE_COST_NCFINLIAB": "以摊余成本计量的非流动金融负债",
    "INSURANCE_CONTRACT_RESERVE": "保险合同准备金",
    "CONVERT_DIFF": "外币报表折算差额",
    "UNCONFIRM_INVEST_LOSS": "未确认的投资损失",
    "TOTAL_OPERATE_INCOME": "营业总收入",
    "OPERATE_INCOME": "营业收入",
    "INTEREST_INCOME": "利息收入",
    "EARNED_PREMIUM": "已赚保费",
    "FEE_COMMISSION_INCOME": "手续费及佣金收入",
    "OTHER_BUSINESS_INCOME": "其他业务收入",
    "TOI_OTHER": "营业总收入其他项目",
    "TOTAL_OPERATE_COST": "营业总成本",
    "OPERATE_COST": "营业成本",
    "INTEREST_EXPENSE": "利息支出",
    "FEE_COMMISSION_EXPENSE": "手续费及佣金支出",
    "RESEARCH_EXPENSE": "研发费用",
    "SURRENDER_VALUE": "退保金",
    "NET_COMPENSATE_EXPENSE": "赔付支出净额",
    "NET_CONTRACT_RESERVE": "提取保险责任准备金净额",
    "POLICY_BONUS_EXPENSE": "保单红利支出",
    "REINSURE_EXPENSE": "分保费用",
    "OTHER_BUSINESS_COST": "其他业务成本",
    "OPERATE_TAX_ADD": "税金及附加",
    "SALE_EXPENSE": "销售费用",
    "MANAGE_EXPENSE": "管理费用",
    "ME_RESEARCH_EXPENSE": "管理费用：研发费用",
    "FINANCE_EXPENSE": "财务费用",
    "FE_INTEREST_EXPENSE": "财务费用：利息费用",
    "FE_INTEREST_INCOME": "财务费用：利息收入",
    "ASSET_IMPAIRMENT_LOSS": "资产减值损失",
    "CREDIT_IMPAIRMENT_LOSS": "信用减值损失",
    "TOC_OTHER": "营业总成本其他项目",
    "FAIRVALUE_CHANGE_INCOME": "公允价值变动收益",
    "INVEST_INCOME": "投资收益",
    "INVEST_JOINT_INCOME": "对联营企业和合营企业的投资收益",
    "NET_EXPOSURE_INCOME": "净敞口套期收益",
    "EXCHANGE_INCOME": "汇兑收益",
    "ASSET_DISPOSAL_INCOME": "资产处置收益",
    "ASSET_IMPAIRMENT_INCOME": "资产减值损失（收益）",
    "CREDIT_IMPAIRMENT_INCOME": "信用减值损失（收益）",
    "OTHER_INCOME": "其他收益",
    "OPERATE_PROFIT_OTHER": "营业利润其他项目",
    "OPERATE_PROFIT_BALANCE": "营业利润平衡项目",
    "OPERATE_PROFIT": "营业利润",
    "NONBUSINESS_INCOME": "营业外收入",
    "NONCURRENT_DISPOSAL_INCOME": "非流动资产处置利得",
    "NONBUSINESS_EXPENSE": "营业外支出",
    "NONCURRENT_DISPOSAL_LOSS": "非流动资产处置损失",
    "EFFECT_TP_OTHER": "影响利润总额的其他项目",
    "TOTAL_PROFIT_BALANCE": "利润总额平衡项目",
    "TOTAL_PROFIT": "利润总额",
    "INCOME_TAX": "所得税费用",
    "EFFECT_NETPROFIT_OTHER": "影响净利润的其他项目",
    "EFFECT_NETPROFIT_BALANCE": "净利润平衡项目",
    "NETPROFIT": "净利润",
    "PRECOMBINE_PROFIT": "被合并方在合并前实现的净利润",
    "CONTINUED_NETPROFIT": "持续经营净利润",
    "DISCONTINUED_NETPROFIT": "终止经营净利润",
    "PARENT_NETPROFIT": "归属于母公司股东的净利润",
    "MINORITY_INTEREST": "少数股东损益",
    "DEDUCT_PARENT_NETPROFIT": "扣除非经常性损益后的净利润",
    "NETPROFIT_OTHER": "净利润其他项目",
    "NETPROFIT_BALANCE": "净利润平衡项目",
    "BASIC_EPS": "基本每股收益",
    "DILUTED_EPS": "稀释每股收益",
    "PARENT_OCI": "归属于母公司股东的其他综合收益",
    "MINORITY_OCI": "归属于少数股东的其他综合收益",
    "PARENT_OCI_OTHER": "归属于母公司股东的其他综合收益其他项目",
    "PARENT_OCI_BALANCE": "归属于母公司股东的其他综合收益平衡项目",
    "UNABLE_OCI": "不能重分类进损益的其他综合收益",
    "CREDITRISK_FAIRVALUE_CHANGE": "信用风险公允价值变动",
    "OTHERRIGHT_FAIRVALUE_CHANGE": "其他权益工具投资公允价值变动",
    "SETUP_PROFIT_CHANGE": "权益法下不能转损益的其他综合收益",
    "RIGHTLAW_UNABLE_OCI": "权益法下不能转损益的其他综合收益",
    "UNABLE_OCI_OTHER": "不能重分类进损益的其他综合收益其他项目",
    "UNABLE_OCI_BALANCE": "不能重分类进损益的其他综合收益平衡项目",
    "ABLE_OCI": "将重分类进损益的其他综合收益",
    "RIGHTLAW_ABLE_OCI": "权益法下可转损益的其他综合收益",
    "AFA_FAIRVALUE_CHANGE": "可供出售金融资产公允价值变动",
    "HMI_AFA": "持有至到期投资重分类为可供出售金融资产损益",
    "CASHFLOW_HEDGE_VALID": "现金流量套期损益的有效部分",
    "CREDITOR_FAIRVALUE_CHANGE": "债权投资公允价值变动",
    "CREDITOR_IMPAIRMENT_RESERVE": "债权投资信用减值准备",
    "FINANCE_OCI_AMT": "金融资产重分类计入其他综合收益的金额",
    "ABLE_OCI_OTHER": "将重分类进损益的其他综合收益其他项目",
    "ABLE_OCI_BALANCE": "将重分类进损益的其他综合收益平衡项目",
    "OCI_OTHER": "其他综合收益其他项目",
    "OCI_BALANCE": "其他综合收益平衡项目",
    "TOTAL_COMPRE_INCOME": "综合收益总额",
    "PARENT_TCI": "归属于母公司股东的综合收益总额",
    "MINORITY_TCI": "归属于少数股东的综合收益总额",
    "PRECOMBINE_TCI": "被合并方在合并前实现的综合收益",
    "EFFECT_TCI_BALANCE": "综合收益总额平衡项目",
    "TCI_OTHER": "综合收益总额其他项目",
    "TCI_BALANCE": "综合收益总额平衡项目",
    "ACF_END_INCOME": "现金流量表期末现金及现金等价物余额",
    "SALES_SERVICES": "销售商品、提供劳务收到的现金",
    "DEPOSIT_INTERBANK_ADD": "客户存款和同业存放款项净增加额",
    "LOAN_PBC_ADD": "向中央银行借款净增加额",
    "OFI_BF_ADD": "向其他金融机构拆入资金净增加额",
    "RECEIVE_ORIGIC_PREMIUM": "收到原保险合同保费取得的现金",
    "RECEIVE_REINSURE_NET": "收到再保业务现金净额",
    "INSURED_INVEST_ADD": "保户储金及投资款净增加额",
    "DISPOSAL_TFA_ADD": "处置交易性金融资产净增加额",
    "RECEIVE_INTEREST_COMMISSION": "收取利息、手续费及佣金的现金",
    "BORROW_FUND_ADD": "拆入资金净增加额",
    "LOAN_ADVANCE_REDUCE": "发放贷款及垫款净减少额",
    "REPO_BUSINESS_ADD": "回购业务资金净增加额",
    "RECEIVE_TAX_REFUND": "收到的税费返还",
    "RECEIVE_OTHER_OPERATE": "收到其他与经营活动有关的现金",
    "OPERATE_INFLOW_OTHER": "经营活动现金流入其他项目",
    "OPERATE_INFLOW_BALANCE": "经营活动现金流入平衡项目",
    "TOTAL_OPERATE_INFLOW": "经营活动现金流入小计",
    "BUY_SERVICES": "购买商品、接受劳务支付的现金",
    "LOAN_ADVANCE_ADD": "发放贷款及垫款净增加额",
    "PBC_INTERBANK_ADD": "存放中央银行和同业款项净增加额",
    "PAY_ORIGIC_COMPENSATE": "支付原保险合同赔付款项的现金",
    "PAY_INTEREST_COMMISSION": "支付利息、手续费及佣金的现金",
    "PAY_POLICY_BONUS": "支付保单红利的现金",
    "PAY_STAFF_CASH": "支付给职工以及为职工支付的现金",
    "PAY_ALL_TAX": "支付的各项税费",
    "PAY_OTHER_OPERATE": "支付其他与经营活动有关的现金",
    "OPERATE_OUTFLOW_OTHER": "经营活动现金流出其他项目",
    "OPERATE_OUTFLOW_BALANCE": "经营活动现金流出平衡项目",
    "TOTAL_OPERATE_OUTFLOW": "经营活动现金流出小计",
    "OPERATE_NETCASH_OTHER": "经营活动产生的现金流量净额其他项目",
    "OPERATE_NETCASH_BALANCE": "经营活动产生的现金流量净额平衡项目",
    "NETCASH_OPERATE": "经营活动产生的现金流量净额",
    "WITHDRAW_INVEST": "收回投资收到的现金",
    "RECEIVE_INVEST_INCOME": "取得投资收益收到的现金",
    "DISPOSAL_LONG_ASSET": "处置固定资产、无形资产和其他长期资产收回的现金净额",
    "DISPOSAL_SUBSIDIARY_OTHER": "处置子公司及其他营业单位收到的现金净额",
    "REDUCE_PLEDGE_TIMEDEPOSITS": "减少质押和定期存款收到的现金",
    "RECEIVE_OTHER_INVEST": "收到其他与投资活动有关的现金",
    "INVEST_INFLOW_OTHER": "投资活动现金流入其他项目",
    "INVEST_INFLOW_BALANCE": "投资活动现金流入平衡项目",
    "TOTAL_INVEST_INFLOW": "投资活动现金流入小计",
    "CONSTRUCT_LONG_ASSET": "购建固定资产、无形资产和其他长期资产支付的现金",
    "INVEST_PAY_CASH": "投资支付的现金",
    "PLEDGE_LOAN_ADD": "质押贷款净增加额",
    "OBTAIN_SUBSIDIARY_OTHER": "取得子公司及其他营业单位支付的现金净额",
    "ADD_PLEDGE_TIMEDEPOSITS": "增加质押和定期存款支付的现金",
    "PAY_OTHER_INVEST": "支付其他与投资活动有关的现金",
    "INVEST_OUTFLOW_OTHER": "投资活动现金流出其他项目",
    "INVEST_OUTFLOW_BALANCE": "投资活动现金流出平衡项目",
    "TOTAL_INVEST_OUTFLOW": "投资活动现金流出小计",
    "INVEST_NETCASH_OTHER": "投资活动产生的现金流量净额其他项目",
    "INVEST_NETCASH_BALANCE": "投资活动产生的现金流量净额平衡项目",
    "NETCASH_INVEST": "投资活动产生的现金流量净额",
    "ACCEPT_INVEST_CASH": "吸收投资收到的现金",
    "SUBSIDIARY_ACCEPT_INVEST": "子公司吸收少数股东投资收到的现金",
    "RECEIVE_LOAN_CASH": "取得借款收到的现金",
    "ISSUE_BOND": "发行债券收到的现金",
    "RECEIVE_OTHER_FINANCE": "收到其他与筹资活动有关的现金",
    "FINANCE_INFLOW_OTHER": "筹资活动现金流入其他项目",
    "FINANCE_INFLOW_BALANCE": "筹资活动现金流入平衡项目",
    "TOTAL_FINANCE_INFLOW": "筹资活动现金流入小计",
    "PAY_DEBT_CASH": "偿还债务支付的现金",
    "ASSIGN_DIVIDEND_PORFIT": "分配股利、利润或偿付利息支付的现金",
    "SUBSIDIARY_PAY_DIVIDEND": "子公司支付给少数股东的股利、利润",
    "BUY_SUBSIDIARY_EQUITY": "购买子公司少数股权支付的现金",
    "PAY_OTHER_FINANCE": "支付其他与筹资活动有关的现金",
    "SUBSIDIARY_REDUCE_CASH": "子公司减资支付给少数股东的现金",
    "FINANCE_OUTFLOW_OTHER": "筹资活动现金流出其他项目",
    "FINANCE_OUTFLOW_BALANCE": "筹资活动现金流出平衡项目",
    "TOTAL_FINANCE_OUTFLOW": "筹资活动现金流出小计",
    "FINANCE_NETCASH_OTHER": "筹资活动产生的现金流量净额其他项目",
    "FINANCE_NETCASH_BALANCE": "筹资活动产生的现金流量净额平衡项目",
    "NETCASH_FINANCE": "筹资活动产生的现金流量净额",
    "RATE_CHANGE_EFFECT": "汇率变动对现金及现金等价物的影响",
    "CCE_ADD_OTHER": "现金及现金等价物净增加额其他项目",
    "CCE_ADD_BALANCE": "现金及现金等价物净增加额平衡项目",
    "CCE_ADD": "现金及现金等价物净增加额",
    "BEGIN_CCE": "期初现金及现金等价物余额",
    "END_CCE_OTHER": "期末现金及现金等价物余额其他项目",
    "END_CCE_BALANCE": "期末现金及现金等价物余额平衡项目",
    "END_CCE": "期末现金及现金等价物余额",
    "ASSET_IMPAIRMENT": "资产减值准备",
    "FA_IR_DEPR": "固定资产折旧、油气资产折耗、生产性生物资产折旧",
    "OILGAS_BIOLOGY_DEPR": "油气资产折耗、生产性生物资产折旧",
    "IR_DEPR": "投资性房地产折旧",
    "IA_AMORTIZE": "无形资产摊销",
    "LPE_AMORTIZE": "长期待摊费用摊销",
    "DEFER_INCOME_AMORTIZE": "递延收益摊销",
    "PREPAID_EXPENSE_REDUCE": "待摊费用减少",
    "ACCRUED_EXPENSE_ADD": "预提费用增加",
    "DISPOSAL_LONGASSET_LOSS": "处置固定资产、无形资产和其他长期资产的损失",
    "FA_SCRAP_LOSS": "固定资产报废损失",
    "FAIRVALUE_CHANGE_LOSS": "公允价值变动损失",
    "INVEST_LOSS": "投资损失",
    "DEFER_TAX": "递延所得税",
    "DT_ASSET_REDUCE": "递延所得税资产减少",
    "DT_LIAB_ADD": "递延所得税负债增加",
    "PREDICT_LIAB_ADD": "预计负债的增加",
    "INVENTORY_REDUCE": "存货的减少",
    "OPERATE_RECE_REDUCE": "经营性应收项目的减少",
    "OPERATE_PAYABLE_ADD": "经营性应付项目的增加",
    "OTHER": "其他",
    "OPERATE_NETCASH_OTHERNOTE": "经营活动现金流量净额其他项目（附注）",
    "OPERATE_NETCASH_BALANCENOTE": "经营活动现金流量净额平衡项目（附注）",
    "NETCASH_OPERATENOTE": "经营活动产生的现金流量净额（附注）",
    "DEBT_TRANSFER_CAPITAL": "债务转为资本",
    "CONVERT_BOND_1YEAR": "一年内到期的可转换公司债券",
    "FINLEASE_OBTAIN_FA": "融资租入固定资产",
    "UNINVOLVE_INVESTFIN_OTHER": "不涉及现金收支的投资和筹资活动其他项目",
    "END_CASH": "现金的期末余额",
    "BEGIN_CASH": "现金的期初余额",
    "END_CASH_EQUIVALENTS": "现金等价物的期末余额",
    "BEGIN_CASH_EQUIVALENTS": "现金等价物的期初余额",
    "CCE_ADD_OTHERNOTE": "现金及现金等价物净增加额其他项目（附注）",
    "CCE_ADD_BALANCENOTE": "现金及现金等价物净增加额平衡项目（附注）",
    "CCE_ADDNOTE": "现金及现金等价物净增加额（附注）",
    "USERIGHT_ASSET_AMORTIZE": "使用权资产摊销",
}


def normalize_a_ticker(code: str) -> tuple[str, str]:
    """返回 (ticker_code, market_code)。纯数字按首位判断沪深北。"""
    text = code.strip().upper()
    if "." in text:
        symbol, market = text.split(".", 1)
        market = market.upper()
        if market not in A_SHARE_MARKETS:
            raise ValueError(f"A 股市场仅支持 SH / SZ / BJ：{code}")
        return f"{symbol.zfill(6)}.{market}", market
    symbol = text.zfill(6)
    if not symbol.isdigit():
        raise ValueError(f"无法识别的 A 股代码：{code}")
    if symbol.startswith("92") or symbol.startswith(("4", "8")):
        market = "BJ"  # 北交所 92xxxx / 83xxxx / 43xxxx；须先于 9 开头的沪市 B 股
    elif symbol.startswith(("6", "9")):
        market = "SH"
    elif symbol.startswith(("0", "2", "3")):
        market = "SZ"
    else:
        raise ValueError(f"无法判断 {symbol} 的市场，请写成 600519.SH 这种形式。")
    return f"{symbol}.{market}", market


def _item_name(field: str, used: set[str]) -> str:
    name = A_SHARE_ITEM_NAMES.get(field, field)
    if name in used:
        name = f"{name}（{field}）"
    used.add(name)
    return name


def eastmoney_row_to_records(row: dict, *, ticker_code: str, market_code: str, item_category: str) -> list[em.FinancialStatementRecord]:
    """把东方财富宽表的一行拆成科目长表。同比列和空值跳过。"""
    report_dt = em.parse_report_date(row.get("REPORT_DATE"))
    period = EM_PERIOD.get(str(row.get("REPORT_TYPE") or "").strip())
    if report_dt is None or period is None:
        return []
    used: set[str] = set()
    records: list[em.FinancialStatementRecord] = []
    for field, raw in row.items():
        if field in EM_META_FIELDS or field.endswith(EM_SKIP_SUFFIXES):
            continue
        amount = em.to_decimal(raw)
        if amount is None:
            continue
        name = _item_name(field, used)
        records.append(
            em.FinancialStatementRecord(
                ticker_code=ticker_code,
                market_code=market_code,
                fiscal_year=report_dt.year,
                report_period=period,
                item_category=item_category,
                source_item_name=name,
                standard_item_name=name,
                source_value=amount,
                standard_value=amount,
                source_unit=em.UNIT_YUAN,
                standard_unit=em.UNIT_YUAN,
            )
        )
    return records


def fetch_eastmoney_statements(ticker_code: str, market_code: str, *, client: em.EastMoneyHkClient | None = None) -> list[em.FinancialStatementRecord]:
    records: list[em.FinancialStatementRecord] = []

    def _collect(active: em.EastMoneyHkClient) -> None:
        for category, report_name in A_SHARE_REPORTS.items():
            for row in active.iter_statement_rows(secucode=ticker_code, item_category=category, report_name=report_name):
                records.extend(eastmoney_row_to_records(row, ticker_code=ticker_code, market_code=market_code, item_category=category))

    if client is not None:
        _collect(client)
    else:
        with em.EastMoneyHkClient() as owned:
            _collect(owned)
    return records


def sina_paper_code(ticker_code: str, market_code: str) -> str:
    symbol = ticker_code.split(".", 1)[0]
    return f"{market_code.lower()}{symbol}"


def sina_report_to_records(payload: dict, *, ticker_code: str, market_code: str, item_category: str) -> list[em.FinancialStatementRecord]:
    """解析新浪 getFinanceReport2022 的一页。只取合并报表，空值和大类标题跳过。"""
    data = ((payload.get("result") or {}).get("data") or {})
    periods = {item["date_value"]: item.get("date_type") for item in data.get("report_date") or []}
    records: list[em.FinancialStatementRecord] = []
    for date_value, report in (data.get("report_list") or {}).items():
        if not isinstance(report, dict):
            continue
        report_type = str(report.get("rType") or "")
        if report_type and not report_type.startswith("合并"):
            continue
        period = SINA_PERIOD.get(periods.get(date_value))
        year_text = str(date_value)[:4]
        if period is None or not year_text.isdigit():
            continue
        used: set[str] = set()
        for item in report.get("data") or []:
            amount = em.to_decimal(item.get("item_value"))
            title = str(item.get("item_title") or "").strip()
            if amount is None or not title:
                continue
            if title in used:
                title = f"{title}（{item.get('item_field') or len(used)}）"
            used.add(title)
            records.append(
                em.FinancialStatementRecord(
                    ticker_code=ticker_code,
                    market_code=market_code,
                    fiscal_year=int(year_text),
                    report_period=period,
                    item_category=item_category,
                    source_item_name=title,
                    standard_item_name=title,
                    source_value=amount,
                    standard_value=amount,
                    source_unit=em.UNIT_YUAN,
                    standard_unit=em.UNIT_YUAN,
                )
            )
    return records


def fetch_sina_statements(ticker_code: str, market_code: str, *, timeout: float = 30.0) -> list[em.FinancialStatementRecord]:
    paper = sina_paper_code(ticker_code, market_code)
    headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn"}
    records: list[em.FinancialStatementRecord] = []
    with httpx.Client(timeout=timeout, headers=headers, trust_env=False) as client:
        for category, source in SINA_SOURCES.items():
            page = 1
            while True:
                response = client.get(SINA_URL, params={"paperCode": paper, "source": source, "type": 0, "page": page, "num": SINA_PAGE_SIZE})
                response.raise_for_status()
                payload = response.json()
                status = ((payload.get("result") or {}).get("status") or {}).get("code")
                if status not in (0, "0", None):
                    raise RuntimeError(f"sina finance report failed: {source} code={status}")
                body = (payload.get("result") or {}).get("data")
                if not body:
                    break
                records.extend(sina_report_to_records(payload, ticker_code=ticker_code, market_code=market_code, item_category=category))
                total = int(body.get("report_count") or 0)
                if page * SINA_PAGE_SIZE >= total:
                    break
                page += 1
                time.sleep(em.EM_REQUEST_PAUSE)
    return records


def _summarize(ticker_code: str, source: str, records: list[em.FinancialStatementRecord], saved: int) -> dict[str, int | str]:
    by_category: dict[str, int] = {}
    for record in records:
        by_category[record.item_category] = by_category.get(record.item_category, 0) + 1
    return {
        "ticker_code": ticker_code,
        "data_source": source,
        "fetched": len(records),
        "saved": saved,
        "balance_sheet": by_category.get("资产负债表", 0),
        "income_statement": by_category.get("利润表", 0),
        "cash_flow": by_category.get("现金流量表", 0),
    }


def sync_a_share_financial_statements(ticker: str, *, source: str = "auto") -> dict[str, int | str]:
    """抓取并入库。source 为 auto / eastmoney / sina。"""
    ticker_code, market_code = normalize_a_ticker(ticker)
    choice = source.strip().lower()
    if choice not in {"auto", "eastmoney", "sina"}:
        raise ValueError("source 只能是 auto、eastmoney 或 sina。")

    records: list[em.FinancialStatementRecord] = []
    used = DATA_SOURCE_EM
    if choice in {"auto", "eastmoney"}:
        try:
            records = fetch_eastmoney_statements(ticker_code, market_code)
        except (httpx.HTTPError, RuntimeError) as exc:
            log.warning("eastmoney a-share statements failed %s: %s", ticker_code, exc)
            if choice == "eastmoney":
                raise
        if records:
            used = DATA_SOURCE_EM
        elif choice == "eastmoney":
            raise RuntimeError(f"东方财富没有返回 {ticker_code} 的财报。")
    if not records:
        records = fetch_sina_statements(ticker_code, market_code)
        used = DATA_SOURCE_SINA
    if not records:
        raise RuntimeError(f"没有抓到 {ticker_code} 的财报。")
    saved = em.save_financial_statements(records)
    return _summarize(ticker_code, used, records, saved)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Sync A-share financial statements into PostgreSQL")
    parser.add_argument("--ticker", required=True, help="A 股代码，如 600519.SH 或 000596")
    parser.add_argument("--source", default="auto", help="auto（默认，东方财富失败再试新浪）/ eastmoney / sina")
    args = parser.parse_args(argv)
    try:
        summary = sync_a_share_financial_statements(args.ticker, source=args.source)
    except Exception as exc:  # noqa: BLE001
        print(f"sync failed: {exc}", file=sys.stderr)
        return 1
    print(f"ticker: {summary['ticker_code']}")
    print(f"source: {summary['data_source']}")
    print(f"fetched: {summary['fetched']}")
    print(f"saved/updated: {summary['saved']}")
    print(f"  资产负债表: {summary['balance_sheet']}")
    print(f"  利润表: {summary['income_statement']}")
    print(f"  现金流量表: {summary['cash_flow']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
