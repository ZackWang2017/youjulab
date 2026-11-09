from __future__ import annotations

import json
import math


def allocation_code(parameters: dict) -> str:
    return ('import bt\nimport pandas as pd\n\n'
            f'PARAMETERS = {parameters!r}\n\n'
            'def build_strategy(prices):\n'
            '    signal = pd.Series(False, index=prices.index)\n'
            '    if PARAMETERS["rebalance"] == "once":\n'
            '        signal.iloc[1] = True\n'
            '    else:\n'
            '        period = {"monthly": "M", "quarterly": "Q", "yearly": "Y"}[PARAMETERS["rebalance"]]\n'
            '        periods = prices.index.to_period(period)\n'
            '        for i in range(1, len(prices)):\n'
            '            signal.iloc[i] = i == 1 or periods[i - 1] != periods[max(0, i - 2)]\n'
            '    class OnSignal(bt.Algo):\n'
            '        def __call__(self, target):\n'
            '            return bool(signal.get(target.now, False))\n'
            '    return bt.Strategy("allocation", [OnSignal(),\n'
            '        bt.algos.WeighSpecified(**PARAMETERS["weights"]), bt.algos.Rebalance()])\n')


def run_backtest(dataset: dict, parameters: dict, targets: list | None = None) -> dict:
    try:
        import bt
        import pandas as pd
    except ImportError as exc:
        raise ValueError("回测依赖未安装，请在后台环境安装 quant_research/requirements.txt。") from exc
    rows = dataset["rows"]
    prices = pd.DataFrame([r[1:] for r in rows], index=pd.to_datetime([r[0] for r in rows]), columns=dataset["symbols"])
    # The warm-up observation is not traded: signals from t execute on the next supplied date.
    if targets is None:
        signal = pd.Series(False, index=prices.index)
        if parameters["rebalance"] == "once":
            signal.iloc[1] = True
        else:
            period = {"monthly": "M", "quarterly": "Q", "yearly": "Y"}[parameters["rebalance"]]
            periods = prices.index.to_period(period)
            for i in range(1, len(prices)):
                signal.iloc[i] = i == 1 or periods[i - 1] != periods[max(0, i - 2)]
        class OnSignal(bt.Algo):
            def __call__(self, target):
                return bool(signal.get(target.now, False))
        algos = [OnSignal(), bt.algos.WeighSpecified(**parameters["weights"]), bt.algos.Rebalance()]
    else:
        if len(targets) != len(prices) or any(not isinstance(w, dict) for w in targets):
            raise ValueError("沙箱输出的目标权重数量无效。")
        for weights in targets:
            if set(weights) - set(dataset["symbols"]) or any(type(v) not in (float, int) or not math.isfinite(v) or v < 0 or v > 1 for v in weights.values()) or abs(sum(weights.values()) - 1) > 1e-7:
                raise ValueError("自定义策略目标权重无效：仅支持无杠杆、非负、合计100%的组合。")
        weights = pd.DataFrame(targets, index=prices.index).fillna(0).shift(1).dropna()
        algos = [bt.algos.WeighTarget(weights), bt.algos.Rebalance()]
    fee = float(parameters.get("fee_bps", 0)) / 10000
    strategy = bt.Strategy("strategy", algos)
    result = bt.run(bt.Backtest(strategy, prices, initial_capital=1_000_000, commissions=lambda quantity, price: abs(quantity * price) * fee, integer_positions=False, progress_bar=False))
    equity = result.backtests["strategy"].strategy.prices.reindex(prices.index)
    if equity.isna().any() or not all(math.isfinite(float(v)) and v > 0 for v in equity):
        raise ValueError("引擎净值校验失败。")
    normalized = equity / float(equity.iloc[0])
    years = max((prices.index[-1] - prices.index[0]).days / 365.25, 1 / 365.25)
    returns = normalized.pct_change().dropna()
    metrics = {"total_return": float(normalized.iloc[-1] - 1), "annual_return": float(normalized.iloc[-1] ** (1 / years) - 1), "max_drawdown": float((normalized / normalized.cummax() - 1).min()), "volatility": float(returns.std(ddof=0) * math.sqrt(len(returns) / years))}
    if any(not math.isfinite(v) for v in metrics.values()):
        raise ValueError("结果指标超出有效范围，请检查数据和区间。")
    return {"metrics": metrics, "series": [[d.strftime("%Y-%m-%d"), float(v)] for d, v in normalized.items()], "basis": dataset["basis"], "data_hash": dataset["sha256"], "source": dataset.get("source"), "observations": len(prices), "execution": "收盘形成信号，下一数据日收盘执行；首个观测日不交易", "validation": "标准引擎净值及权重检查通过；不代表研究逻辑或经济有效性已被证明", "engine": "bt", "engine_version": getattr(bt, "__version__", "unknown")}

