#!/bin/bash
# 首次初始化数据卷时执行。finresearch 由 POSTGRES_DB 创建；这里补 office_vectors 与 pgvector。
set -euo pipefail

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres -tc \
  "SELECT 1 FROM pg_database WHERE datname = 'office_vectors'" | grep -q 1 \
  || psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres -c \
    "CREATE DATABASE office_vectors"

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname office_vectors -c \
  "CREATE EXTENSION IF NOT EXISTS vector"
