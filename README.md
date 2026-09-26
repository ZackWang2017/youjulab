# Office Copilot MVP

本地的人与 AI 协作工作台：Univer 表格、Tiptap 富文本、PDF.js 阅读器、Mind Elixir 思维导图、
FullCalendar 工作计划（农历与法定休/班）。
三个编辑器将选区发送到 `apps/office-api`；Agent 按工作区加载
`agents/*.agent.yaml` 与 `skills/*/SKILL.md`，流式输出回答或待确认的修改。

## 本地启动

需要 Node.js 22.13+、Python 3.11+、MongoDB Community 8.0。在项目根目录：

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r mcp-server/requirements.txt
.venv/bin/python -m pip install -r apps/office-api/requirements.txt
cd apps/office-copilot-web
npm ci
```

### MongoDB

office-api 连接 `mongodb://127.0.0.1:27017`，库名 `office`。根目录 `.env`：

```sh
OFFICE_MONGO_URL=mongodb://127.0.0.1:27017
OFFICE_MONGO_DB=office
```

macOS（Homebrew）首次安装并开机自启：

```sh
brew tap mongodb/brew
brew trust mongodb/brew
brew install mongodb-community@8.0 --without-mongosh
brew services start mongodb/brew/mongodb-community@8.0
```

日常启动 / 停止 / 状态：

```sh
brew services start mongodb/brew/mongodb-community@8.0
brew services stop mongodb/brew/mongodb-community@8.0
brew services info mongodb/brew/mongodb-community@8.0
```

`mongod` 监听 `127.0.0.1:27017`。安装时加 `--without-mongosh` 是为了避开本机 Homebrew 缺少 `simdutf` bottle；需要命令行壳可以另装 `mongosh`。

### 三个进程

终端 1，Agent / 结构化查询（默认 8000）：

```sh
FINANCE_MCP_TRANSPORT=http FINANCE_MCP_HOST=127.0.0.1 .venv/bin/python mcp-server/server.py
```

终端 2，登录、项目与文档快照（默认 8100，需 MongoDB 已启动）：

```sh
.venv/bin/python apps/office-api/server.py
```

终端 3，前端：

```sh
cd apps/office-copilot-web
npm run dev
```

访问终端输出的地址，默认 http://127.0.0.1:5173。Vite `/backend` 代理到 MCP，
`/office` 代理到 office-api。改端口时在根目录 `.env` 设置 `FINANCE_MCP_PORT`
或 `OFFICE_API_PORT`，并重启对应进程。临时不用 Mongo 时可设
`OFFICE_MONGO_URL=memory://office`（进程退出后数据清空）。

Univer 表格引擎依赖 `new Function()`，前端 CSP 需包含 `script-src 'unsafe-eval'`
（见 `apps/office-copilot-web/csp.ts`）。若 IDE 内置浏览器仍报 CSP 阻止 eval，
请改用系统 Chrome / Safari 打开同一地址；内置预览的 CSP 有时比页面自身策略更严。

## 数据备份

数据库备份（Mongo + Postgres，不含本地 PDF）见 [`backup/README.md`](backup/README.md)：

```sh
./backup/backup.sh
./backup/restore.sh backup/archives/YYYYMMDD-HHMMSS.tar.gz
```

## 模型

所有用户共用 office-api 后台配置的同一个模型，前端不再选择运行模式。
在根目录 `.env` 填写 DeepSeek（或其他 OpenAI 兼容接口）后重启 office-api：

```sh
OFFICE_MODEL_BASE_URL=https://api.deepseek.com/v1
OFFICE_MODEL_NAME=deepseek-chat
OFFICE_MODEL_API_KEY=你的密钥
```

`OFFICE_MODEL_BASE_URL` 应包含 `/v1`。接口需支持流式 `POST /chat/completions` 与
`response_format: {type: json_object}`。密钥只在服务端使用，回答会流式显示在协作助手中。

## 外部工具 MCP：网页搜索

独立入口（**不**挂在财务 `/mcp` 或表格 `/mcp/sheets`）：

| 项 | 值 |
|----|----|
| MCP | `invp-external-tools` |
| URL | `http://127.0.0.1:8000/mcp/external-tools` |
| 鉴权 | 与财务相同，`.env` 的 `MCP_ACCESS_TOKEN`（Bearer） |
| 实现 | `mcp-server/external_tools/search_engine.py` |
| Skill | `skills/common/external-tools.md`（专题 skill include） |

工具：`baidu_web_search` / `baidu_baike_search` / `google_web_search`。`topic`
（`finance` | `accounting` | `encyclopedia`）只用于网页搜。百科工具走词条接口
`lemma/get_content`，不是百度网页搜索加站点过滤。

**Google 配置模型（无法全网搜）：** 只用 **一个** `GOOGLE_CSE_CX`，控制台「要搜索的网站」填入三组 `SEARCH_SITES_*` 的**并集**；调用时若带 `topic`，仍用同一 CX，再用对应子集做 `site:` 收窄。

### 搜索开关与每日限额

计数保存在 Postgres（`OFFICE_PG_URL`）表 `external_search_daily`；启动 MCP 时建表并读取当日已用次数。时区：`SEARCH_USAGE_TZ`（默认同 `DIGEST_TZ` / `Asia/Shanghai`）。

```sh
SEARCH_BAIDU_ENABLED=1
SEARCH_BAIKE_ENABLED=1
SEARCH_GOOGLE_ENABLED=1
SEARCH_BAIDU_DAILY_LIMIT=100
SEARCH_BAIKE_DAILY_LIMIT=100
SEARCH_GOOGLE_DAILY_LIMIT=100
SEARCH_USAGE_TZ=Asia/Shanghai
```

- `*_ENABLED=0`：手动关闭该引擎。
- 当日 `count >= *_DAILY_LIMIT`：自动关闭，工具返回 `quota.auto_closed`。
- 可调 `search_quota_status` 查看今日用量。

改 `.env` 后需**重启** mcp-server。只配其中一个引擎也可以，对应工具可用即可。

### 垂直主题站点（与单一 CSE 合并）

```sh
GOOGLE_CSE_API_KEY=你的_API_Key
GOOGLE_CSE_CX=你的搜索引擎ID   # 控制台收录下方三组全部域名

SEARCH_SITES_FINANCE=finance.eastmoney.com,www.cninfo.com.cn,www.sse.com.cn,www.szse.cn,www.cls.cn,finance.sina.com.cn,www.stcn.com,www.cs.com.cn,www.yicai.com,www.wallstreetcn.com,www.investing.com,finance.yahoo.com
SEARCH_SITES_ACCOUNTING=kjs.mof.gov.cn,www.casc.org.cn,www.cicpa.org.cn,www.mof.gov.cn,www.csrc.gov.cn,www.ifrs.org,www.fasb.org,www.sec.gov
SEARCH_SITES_ENCYCLOPEDIA=baike.baidu.com,zh.wikipedia.org,en.wikipedia.org,wiki.mbalib.com,www.investopedia.com
# 可选：显式全集（默认自动合并上面三组）
SEARCH_SITES=
```

| 调用 | Google 行为 | 百度行为 |
|------|-------------|----------|
| `topic=""` | 在 CSE 已收录的全部站点内搜 | 全网搜 |
| `topic=finance` 等 | 同一 CX + 该主题 `SEARCH_SITES_*` 的 `site:` 过滤 | 该主题站点过滤 |
| `baidu_baike_search` | — | 百科词条 `lemma/get_content`，不是网页搜索 |

| topic | 覆盖 |
|-------|------|
| `finance` | 东财、巨潮、两所、财联社、证时报/中证报、一财、见闻、Investing、Yahoo Finance 等 |
| `accounting` | 会计司、准则委、中注协、财政部、证监会、IFRS、FASB、SEC |
| `encyclopedia` | 百度百科、维基、MBA 智库、Investopedia |

### 已接入：百度千帆（`BAIDU_QIANFAN_API_KEY`）

适合中文查询。调用千帆 [web_search](https://cloud.baidu.com/doc/qianfan-api/s/Wmbq4z7e5)。

1. 注册并实名 [百度智能云](https://cloud.baidu.com/)。
2. 打开 [千帆控制台](https://console.bce.baidu.com/qianfan/overview) → **系统管理 → API Key** → 创建（服务选千帆平台）。
3. 确认已开通 **百度 AI 搜索**（`web_search` 有免费额度，超量需后付费）。
4. 将 Key 写入根目录 `.env`：

```sh
BAIDU_QIANFAN_API_KEY=bce-v3/ALTAK-...你的密钥...
```

文档：[API Key](https://cloud.baidu.com/doc/qianfan/s/wmh8l6tnf)

**百度百科**（`baidu_baike_search`）复用同一 `BAIDU_QIANFAN_API_KEY`，调用
`https://appbuilder.baidu.com/v2/baike/lemma/get_content`（按词条名取概述）。
词条没有概述时再查 `get_list_by_title`。独立开关
`SEARCH_BAIKE_ENABLED` / `SEARCH_BAIKE_DAILY_LIMIT`。与 `baidu_web_search` 不是同一接口。

### 已接入：Google Custom Search（`GOOGLE_CSE_API_KEY` + `GOOGLE_CSE_CX`）

适合英文 / 国际来源。免费额度约 **100 次/天**，超出需开通计费。

**A. API Key（Google Cloud）**

1. [Google Cloud Console](https://console.cloud.google.com/) 新建或选择项目。
2. **API 和服务 → 库** → 启用 **Custom Search API**。
3. **凭据 → 创建凭据 → API 密钥** → 即为 `GOOGLE_CSE_API_KEY`。

**B. 唯一搜索引擎 ID（垂直站并集，非全网）**

1. 打开 [Programmable Search Engine](https://programmablesearchengine.google.com/)。
2. 添加**一个**搜索引擎，把 `SEARCH_SITES_FINANCE` + `ACCOUNTING` + `ENCYCLOPEDIA` 的域名全部填进「要搜索的网站」。
3. 复制 **Search engine ID** → `GOOGLE_CSE_CX`。

未配主题专用 CX 时，调用会在 query 上附加 `site:域名 OR ...`。**仍请在 CSE 控制台收录这些站**。

若出现 HTTP 403 `This project does not have the access to Custom Search JSON API`：
到 [Google Cloud Console](https://console.cloud.google.com/) 打开 **API 和服务 → 库**，
对当前项目启用 **Custom Search API**，并确认计费账号/配额可用后重试。

文档：[Custom Search JSON API](https://developers.google.com/custom-search/v1/introduction)

### 免费 / 低成本替代（可选后端）

官方百度、Google 之外，常见免费或有免费档的方案如下。**当前代码默认只接百度 + Google**；若要接下列后端，可在 `search_engine.py` 增适配器，或先自建后用 HTTP 代理。

| 方案 | 费用大致 | 如何获取 / 配置 | 说明 |
|------|----------|-----------------|------|
| **SearXNG（自建）** | 免费 | Docker 自建实例，开启 JSON：`GET {base}/search?q=...&format=json`。许多公共实例关闭了 `format=json`，**请自建**。 | 开源元搜索，无 key；质量取决于上游引擎，需运维。适合零申请本地开发。 |
| **Brave Search API** | 约每月 $5 额度（≈1000 次） | [brave.com/search/api](https://brave.com/search/api/) 申请；常需绑卡防滥用。环境变量建议：`BRAVE_SEARCH_API_KEY` | 官方独立索引，比 Google CSE 日配额更宽松，英文较好。 |
| **Tavily** | 免费档约 1000 次/月 | [tavily.com](https://tavily.com/) 注册拿 API Key。建议：`TAVILY_API_KEY` | 面向 Agent 的结构化搜索；中文弱于百度。 |
| **Serper 等 SERP** | 常有试用额度 | 各厂商控制台申请 Key | 多为 Google 结果代理，有配额，非无限免。 |
| **DuckDuckGo** | 无官方通用网页 API | 仅非官方 HTML/库 | 易限流、易挂，不建议生产主依赖。 |
| **Wikipedia API** | 免费 | MediaWiki API | 只覆盖百科，不能替代全网搜。 |

**SearXNG 自建示例（Docker）：**

```sh
docker run -d --name searxng -p 8080:8080 \
  -v "$(pwd)/searxng:/etc/searxng" \
  searxng/searxng:latest
```

在实例配置中启用 `json` 输出格式后，本地调试可用：

```sh
curl -s 'http://127.0.0.1:8080/search?q=市盈率&format=json' | head
```

接 MCP 时建议环境变量：`SEARXNG_BASE_URL=http://127.0.0.1:8080`（实现适配器后再写入工具表）。

**选用建议：** 中文财经优先百度免费额度；零成本本地优先 SearXNG；英文低成本优先 Brave / Tavily 免费档。没有「官方稳定 + 无限免费 + 中文质量接近百度/Google」的单一方案。

## 当前范围

- 表格：原生选择、编辑、公式，选区分析与矩阵替换，预览、接受、撤销。
- 文档：富文本编辑、加粗/斜体/标题/列表、选区处理；单段落内替换保留起点的行内样式。
- PDF：上传到当前项目，连续滚动、跳转翻页、仅缩放 PDF 区域、文字选择、高亮与文字批注（写回 PDF 文件）、页码和 PDF 坐标引用；只有选区和页内文本发送到 Agent。
- 思维导图：Mind Elixir 编辑；资产 `kind=mindmap`，`content` 为 `nodeData` 树 JSON；选中节点可解释/改写主题，生成内容会插入子节点。
- 工作计划：FullCalendar 月/周/日视图；资产 `kind=calendar`，`content.days` 为按日 Markdown；双击日期编辑当天内容。日期格显示农历与法定休/班（见下节「法定节假日数据更新」）。
- 修改绑定原文档、选区和版本，内容变化后旧补丁不能应用。
- 登录后按用户隔离项目；每个项目可有多份表格、文档、PDF、导图、计划。上次项目与文件记在服务端。
- 表格、文档、导图、计划 JSON 自动保存到 office-api（MongoDB）；PDF 文件存在本地目录。
- 当前 Agent 为单轮、按工作区路由的执行器，真实读取 Skills，调用模型并校验返回值。

### 法定节假日数据更新

工作计划里的**农历**按算法计算，一般无需单独更新。**法定放假 / 调休上班**（休、班圆标与节日名）来自前端依赖
[`lunar-javascript`](https://www.npmjs.com/package/lunar-javascript) 的 `HolidayUtil`
（国务院办公厅公布的安排，打包在库内）。实现见
`apps/office-copilot-web/src/editors/calendar/day-meta.ts`。

当前库版本法定数据大致覆盖到 **2026 年国庆**；其后年份或国务院新通知若尚未写入 npm，
翻到对应月份时不会出现休/班，这是数据范围问题，不是日历格子坏了。

**方式一：升级依赖（推荐）**

作者把下一年安排发到新版本后，在前端目录执行并重启 `npm run dev`：

```sh
cd apps/office-copilot-web
npm view lunar-javascript version    # 查看最新版
npm update lunar-javascript
# 或
npm install lunar-javascript@latest
```

**方式二：运行时临时补录**

等不及发版时，可在应用启动逻辑里调用 `HolidayUtil.fix(...)` 追加编码串（格式见该包
`__tests__/Holiday.test.js`：每段含日期、类型与目标日）。适合国务院通知刚出、npm 尚未更新的窗口期；正式环境仍以升级依赖为准。

农历本身不依赖上述节假日表；只更新休/班与法定节日名。

尚未实现 XLSX/DOCX 导入导出、JSON 导入、PDF OCR、跨段富文本替换、
Yjs 多人协同、项目分享以及模型自主多轮工具调用。
现有财务工具需要其原有档案配置，MVP 不依赖这些档案。

## 目录与扩展

```text
apps/office-copilot-web/src/
  editors/{sheet,document,pdf,mindmap,calendar}/   编辑器适配器
  context/                       统一选区与补丁类型
  workspace/                     项目与资产会话、导航
  office-client/                 工作区 HTTP 客户端
  patches/                       修改预览
  mcp-client/                    Agent HTTP 适配器
apps/office-api/                 登录、项目、资产、PDF 文件
agents/                          主路由及专业 Agent 配置（按表格/文档/PDF/导图/计划 × 选区解释/生成内容）
skills/{spreadsheet,document,pdf,mindmap,calendar}/SKILL.md
skills/{spreadsheet-generate,document-generate,mindmap-generate,calendar-generate}/SKILL.md
skills/common/contracts.md       共享约定（协作助手）
skills/common/external-tools.md  外部搜索 MCP 共用约定（专题 skill include）
invp-skills/                     外部厂商 Agent 的表格 MCP skills
  invp-skills-chatgpt/
  invp-skills-workbuddy/
  invp-skills-deepseek/
mcp-server/
  server.py                      FastMCP 入口（财务 /mcp + 表格 /mcp/sheets + 外部工具 /mcp/external-tools）
  external_tools/                外部工具包（搜索等，不挂 finresearch）
    search_engine.py             百度千帆 / Google CSE 网页搜索
  access_token.py                财务 MCP 静态令牌；表格入口转发用户 token
  office-api/                    Office 相关 MCP/REST（与财务 API 分开）
    office_sheets.py             按项目名/表名/sheet名创建、读写、合并与样式
    office_workbook.py           Univer JSON 单元格写入（不渲染）
    office_sheet_client.py       转发到 apps/office-api
    office_sheet_routes.py       /api/spreadsheets/* 与 /mcp/sheets
    office_context.py            选区验证
    office_patch.py              补丁生成及冲突校验
    office_agent.py              Skills 加载与模型调用
    office_routes.py             选区 Agent REST /api/office
  tests/test_office.py            办公协议测试
  tests/test_external_tools_search.py  搜索引擎 mock 测试
```

已有 MCP 入口 `/mcp` 或 stdio 提供财务工具及 `get_office_context`、`run_office_agent`、
`validate_office_patch`。浏览器协作助手走 office-api，不走该入口。
外部 Agent 读写表格走同一 mcp-server 进程的 `/mcp/sheets` 与
`POST /api/spreadsheets/create|read|write|merge|unmerge|background|border|font`，参数是项目名、表名、sheet 名。
网页搜索走同一进程的 **`/mcp/external-tools`**（见上文「外部工具 MCP」）。
表格鉴权使用办公工作区每个用户自己的 32 位访问令牌。令牌只用来找到登录邮箱账号，项目和表格挂在该账号下；重新生成令牌不会丢掉资产，但需换新 skill。
财务 `/mcp` 与外部工具 `/mcp/external-tools` 仍用 `.env` 的 `MCP_ACCESS_TOKEN`。
不要把表格或外部搜索工具注册进财务 `/mcp`，也不要走 `/api/financial` 或 PDF 渲染。
厂商 skill 放在 `invp-skills/<厂商名>/`，与协作助手的 `skills/` 分开。
新增协作助手 Skill 时创建 `skills/<name>/SKILL.md`，将专业 Agent 配置中的 `skill`
指向它。文件在每次请求时读取，无需重启即可调整提示词。

## 验证

```sh
.venv/bin/python -m unittest discover -s mcp-server/tests -v
.venv/bin/python -m unittest discover -s apps/office-api/tests -v
cd apps/office-copilot-web
npm run build
```

浏览器回归脚本位于 `apps/office-copilot-web/tests/browser-smoke.cjs`，需要前后端
运行、安装 Chrome，且 Node 能加载 Playwright。可用 `PLAYWRIGHT_MODULE` 指定
已有 Playwright 模块路径，在前端目录执行 `node tests/browser-smoke.cjs`。
脚本生成临时 PDF，验证三个编辑器的选区与操作，以及手机页面宽度。

开源项目资料：[Univer](https://github.com/dream-num/univer)、
[Tiptap](https://github.com/ueberdosis/tiptap)、[PDF.js](https://github.com/mozilla/pdf.js)、
[Mind Elixir](https://github.com/SSShooter/mind-elixir-core)、
[FullCalendar](https://fullcalendar.io/)、
[lunar-javascript](https://github.com/6tail/lunar-javascript)。
详细规划见 `docs/human-ai-office-collaboration-architecture.md`。

ollama ebedding service
ollama pull bge-m3
tail -f ~/.ollama/logs/server.log
