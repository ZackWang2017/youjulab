# finreport —— 读财报数据服务（财报解析 + 浏览器 REST 端点）
#
# 构建：docker build -t finreport .
# 运行：docker run -d --name finreport -p 8000:8000 \
#         -e FINANCE_MCP_TRANSPORT=http \
#         -v finreport-uploads:/app/uploads \
#         finreport
# 说明：
#   - 默认监听 0.0.0.0:8000，提供 /mcp（MCP JSON-RPC）、/upload、/parse、
#     /page-text、/page-image、/pdf 端点，CORS 全开（供 dsh Web 浏览器直连）。
#   - /upload 与解析默认以 /app/uploads 为根（FINANCE_PDF_ROOT）；
#     把 PDF 放进来即挂载卷，会话里用绝对路径解析。
#   - 需要鉴权时加 -e FINANCE_MCP_TOKEN=xxx（所有端点检查 Bearer）。

FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY mcp-server/ ./mcp-server/

ENV FINANCE_MCP_TRANSPORT=http \
    FINANCE_MCP_HOST=0.0.0.0 \
    FINANCE_MCP_PORT=8000 \
    FINANCE_PDF_ROOT=/app/uploads

RUN mkdir -p /app/uploads

EXPOSE 8000

CMD ["python", "mcp-server/server.py"]
