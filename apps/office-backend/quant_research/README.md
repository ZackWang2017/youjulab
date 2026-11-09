# 量化研究资产 MVP

代码属于 office-backend。前端入口位于 PDF 下方，资产类型为 `strategy`，内容格式为 `youju-strategy-v1`，沿用用户—项目—资产、可见范围及 revision 冲突检查。

## 已实现

- 自然语言研究目标、多轮澄清、现有用户模型配置、Thinking 事件。
- 有限工具路由：固定配置 `bt_allocation`、自定义价格策略 `python_research`、缺数据时 `needs_input`。
- 生成 JSON/代码最多三次校验修复；不执行模型生成的宿主代码。
- CSV 真数据导入、数据来源/价格或全收益口径、严格日期和正数检查、数据指纹。
- bt 固定配置：买入持有、月/季/年再平衡、比例费用、浮点份额；收盘形成信号、下一数据日收盘执行。首个数据日不交易。
- 自定义 Python：目标权重函数在 Docker 沙箱计算，再由可信 bt 引擎处理组合。没有沙箱时停止并显示错误，不在宿主执行。
- 任务异步执行、不可变源码/数据/参数快照、请求幂等、任务取消、结果及日志持久化。
- SQLite 部署把 `quant_runs` 表放在现有 `office.sqlite` 内，因此原有 office 数据库备份会包含研究记录；不要直接修改运行中数据库，原资产存储还维护内存状态。
- 程序自动保存/手动保存、代码下载、净值图、核心指标、历史记录和不同版本对比。

## 当前边界

- 当前不自动下载历史行情，也没有接入完整 PIT 基本面、历史成分股和公司行动服务。CSV 需使用同币种、已对齐交易日、无缺失值的数据。
- Agent 只获得数据覆盖摘要，不把完整价格序列发送到模型。
- 固定配置采用服务端生成的规范程序与可信实现；修改程序会转为自定义 Python 路径，不忽略修改后继续跑固定配置。
- 自定义目标权重非负、合计100%，不支持卖空、杠杆、订单级撮合。
- 沙箱按日传入历史窗口，但自定义代码仍可能尝试读取挂载的完整快照，因此结果明确标注探索性；窗口接口不构成无未来信息的数学证明。
- 只有预算内的程序生成修复，未实现 RD-Agent/Qlib、无限研究搜索、因子专用统计或自动回测后调参；不会冒充这些能力已可用。
- 运行期间刷新页面可查询任务；后台进程重启后未完成任务标记中断失败，保留原版本供重跑，不宣称已经实现断点续算。
- 当前支持少量任务并发；多 worker 共享同库、故障接管、长期保留策略需要后续任务租约设计。
- 年化指标是区间机械年化，短期数据不能理解为长期年化表现。

## 安装和启动

```sh
.venv/bin/python -m pip install -r apps/office-backend/quant_research/requirements.txt
.venv/bin/python apps/office-backend/server.py
```

安装依赖和重启后，不需要重新构建桌面壳。

若启用自定义代码执行，由部署管理员构建专用镜像：

```sh
docker build -t youju-quant:1 apps/office-backend/quant_research
```

`OFFICE_QUANT_IMAGE` 默认为 `youju-quant:1`。不要挂载 Docker socket、用户令牌、模型密钥、宿主数据库或 `.env` 给研究任务。生产多用户部署应采用 gVisor 或单独隔离主机，不能把普通容器当成绝对安全边界。

镜像启动参数由服务端固定：无网络、非 root、只读文件系统、独立临时目录、1GB内存/1CPU/64进程、120秒超时、输出大小限制。

## CSV

```csv
date,标的A,标的B
2020-01-02,100,100
2020-01-03,101,99
2020-01-06,102,100
```

最大4MB、12000日、30标的。示例仅说明格式，不作为真实行情或投资结论。

## 自定义价格研究接口

```python
def target_weights(history, parameters):
    # history: pandas DataFrame，索引为日期，列与CSV标的一致。
    # 当日输出在下一数据日收盘执行。
    symbols = list(history.columns)
    return {symbol: 1 / len(symbols) for symbol in symbols}
```

## 主要接口

- GET `/quant/capabilities`
- PUT `/assets/{id}/strategy/data`
- POST `/assets/{id}/strategy/research`（SSE）
- GET/POST `/assets/{id}/strategy/runs`
- GET `/assets/{id}/strategy/runs/{run_id}`
- POST `/assets/{id}/strategy/runs/{run_id}/cancel`

资产 API 管理研究说明、程序和数据；运行 API 冻结并保留对应版本。所有接口校验用户权限，运行ID不能跨用户或跨资产读取。

## 验证

```sh
.venv/bin/python -m unittest discover -s apps/office-backend/tests -p test_quant_research.py
```

测试覆盖数据验证、数值口径、次日执行、费用、权限、冲突、Agent工具选择（模型替身）、任务持久化和无沙箱失败。浏览器检查也使用独立测试用户/合成数据，不修改用户真实研究资产，不将测试结果作为真实投资收益。
