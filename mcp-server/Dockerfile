# 构建上下文是仓库根目录：
#   docker build -f mcp-server/Dockerfile -t youjulab-mcp .
FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    FINANCE_MCP_TRANSPORT=http \
    FINANCE_MCP_HOST=0.0.0.0 \
    FINANCE_MCP_PORT=8000 \
    FINANCE_PDF_ROOT=/app/data

COPY mcp-server/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

COPY mcp-server /app/mcp-server
COPY deploy/entrypoint-mcp.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

WORKDIR /app/mcp-server
EXPOSE 8000

CMD ["/entrypoint.sh"]
