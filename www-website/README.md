# 有据 youjulab 官网

纯静态三页产品介绍网站。不依赖 Node、Python、数据库、CDN 或构建工具。
直接打开 `index.html` 即可预览，部署时由 Nginx 提供静态文件。

## 页面

- `index.html`：品牌首页、工作区示意、核心价值与系统入口。
- `features.html`：可切换的工具展示、持仓资讯、AI 协作能力。
- `workflow.html`：公司研究工作流、使用理念与常见问题。
- `assets/`：样式、脚本、本地 Lucide 图标库、品牌与产品示意图。
- `design/workspace.html`：工作区示意图的设计源稿，不是真实用户截图。

主域名：https://www.youjulab.com

系统入口：https://app.youjulab.com

## 设计说明

白底、墨绿色文字、细分隔线与留白；使用现有有据品牌图标。
以“资料 → 理解 → 假设 → 验证”组织叙事，不展示虚构用户数量、
客户评价或投资收益。财务数值均标注为示例。

“投资模型研究”限定为表格估值、财务假设与情景分析，未把暂时
关闭的策略 Agent 和自动回测作为已上线能力宣传。

## 部署（安装好 Nginx 后再操作）

将 `index.html`、`features.html`、`workflow.html`、`assets/`、
`robots.txt` 和 `sitemap.xml` 上传至同一个网站根目录。
`README.md`、`nginx.conf.example` 和 `design/` 无需上传。

参考 `nginx.conf.example`，替换其中网站根目录。HTTPS 证书需要
自行申请和配置；示例仅提供 HTTP，避免引用不存在的证书导致
Nginx 无法启动。完成 DNS 和证书配置后再启用 HTTPS 重定向。

官网没有更改 app 子域名服务，也不代理系统 API。

如在中国大陆服务器公开上线，请按实际情况完成备案并添加真实
备案号；当前没有放置虚构备案号或未经确认的联系方式。

## 修改

公共导航、页脚及工具示例在 `assets/site.js`；样式在
`assets/site.css`；页面正文在各自 HTML。脚本不收集访客信息，
没有分析追踪、表单或第三方远程资源。

示意图可从 `design/workspace.html` 渲染为 1600×960 PNG，替换
`assets/workspace-preview.png`。以后可替换为脱敏后的真实截图。

图标库许可证：`assets/vendor/LUCIDE-LICENSE`。
