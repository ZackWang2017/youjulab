from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from .repository import RunRepository
from .engine import allocation_code, run_backtest
from .sandbox import run_code


class ResearchService:
    def __init__(self, repository: RunRepository):
        self.repository = repository
        self.pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="quant-research")

    def start(self, run: dict):
        self.pool.submit(self.execute, run)

    def execute(self, run: dict):
        repo = self.repository
        run_id = run["id"]
        cancelled = lambda: (repo.get(run_id) or {}).get("status") == "cancelled"
        try:
            if cancelled():
                return
            repo.update(run_id, status="running", message="检查真实数据、程序与已注册工具。")
            content = run["snapshot"]["content"]
            plan, dataset = content["plan"], content["dataset"]
            targets = None
            if plan["tool"] == "bt_allocation":
                if content["code"] != allocation_code(plan["parameters"]):
                    raise ValueError("固定配置程序已被修改，请重新生成计划或切换为自定义 Python 研究。")
                repo.update(run_id, message="执行 bt 固定权重再平衡；生成代码与参数一致。")
            else:
                repo.update(run_id, message="在无网络隔离沙箱中计算历史目标权重。")
                targets = run_code(content["code"], dataset, plan["parameters"], run_id, cancelled)
            if cancelled():
                return
            repo.update(run_id, status="validating", message="使用标准引擎计算持仓与费用，检查权重及净值。")
            result = run_backtest(dataset, plan["parameters"], targets)
            if plan["tool"] == "python_research":
                result["validation"] += "；自定义程序为探索结果，尚未证明不存在未来信息泄漏。"
            if not cancelled():
                repo.update(run_id, status="completed", result=result, message="运行完成，结果、数据指纹和源码版本已保存。")
        except Exception as exc:
            if not cancelled():
                repo.update(run_id, status="failed", error=str(exc)[:2000], message="任务停止，未生成有效回测结论。")

