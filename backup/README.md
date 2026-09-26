# 数据库备份与恢复

备份 **MongoDB（办公资产）** 与 **Postgres（向量 / 搜索配额 / 可选财务库）**。
**不备份** 本地 PDF 目录 `OFFICE_FILE_ROOT`（默认 `data/office-files`）。

## 依赖

- `mongodump` / `mongorestore`（[MongoDB Database Tools](https://www.mongodb.com/docs/database-tools/)）
- `pg_dump` / `pg_restore`（与本机 Postgres 同套）
- 仓库根目录已配置 `.env`

macOS 示例：

```sh
brew install mongodb/brew/mongodb-database-tools
# pg_dump 一般随 Postgres / libpq 安装
```

## 备份

在仓库根目录：

```sh
chmod +x backup/backup.sh backup/restore.sh
./backup/backup.sh
```

产物：

```text
backup/archives/YYYYMMDD-HHMMSS/
  mongodb/<库名>/          # mongodump
  office_pg.dump           # OFFICE_PG_URL（若已配置）
  finance_pg.dump          # FINANCE_DATABASE_URL（若已配置）
  manifest.json
  RESTORE.txt
backup/archives/YYYYMMDD-HHMMSS.tar.gz
```

常用参数：

```sh
./backup/backup.sh --keep 7                 # 只保留最近 7 份压缩包
./backup/backup.sh --out /Volumes/Backup/invp
```

## 恢复到新服务器

1. 安装 MongoDB、Postgres（办公向量库建议装好 **pgvector**）。
2. 部署本仓库，写好新机 `.env`（`OFFICE_MONGO_*`、`OFFICE_PG_URL`、可选 `FINANCE_DATABASE_URL`）。
3. 将 `.tar.gz` 拷到新机后：

```sh
./backup/restore.sh backup/archives/YYYYMMDD-HHMMSS.tar.gz
# 或
./backup/restore.sh /path/to/YYYYMMDD-HHMMSS.tar.gz --yes
```

会 **覆盖** 目标库中同名数据。PDF 元数据会恢复，但文件需重新上传，或自行拷贝原机 `OFFICE_FILE_ROOT` 后再跑索引。

## 备份范围对照

| 内容 | 是否备份 |
|------|----------|
| 用户 / 项目 / 表格·文档·导图 JSON | 是（Mongo） |
| PDF 资产元数据（名称、路径字段等） | 是（Mongo） |
| PDF 二进制文件 | **否** |
| PDF 向量索引、搜索日配额 | 是（`OFFICE_PG_URL`，若配置） |
| 财务 MCP 证券/财报表 | 是（`FINANCE_DATABASE_URL`，若配置） |
| `.env` 密钥 | **否**（请单独安全转移） |
