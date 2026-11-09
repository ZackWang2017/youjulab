# Multiuser Desktop / Remote MCP

## Boundaries

Each desktop owns its Office SQLite database, PDFs and sqlite-vec index. The remote
MCP service owns account/password hashes, portfolios and public financial data in PostgreSQL.
Existing Office asset paths and local account IDs are unchanged. Remote account IDs and
password hashes must be copied unchanged during migration. Do not share an OS user-data
directory over a network drive. Project visibility does not imply cross-device synchronization.

## Server Configuration

Install the existing Python requirements for `mcp-server` and provision PostgreSQL yourself.
Use `.env.example` as a reference, with private server-side values:

```dotenv
FINANCE_MCP_TRANSPORT=http
FINANCE_MCP_HOST=0.0.0.0
FINANCE_MCP_PORT=8000
FINANCE_STORE=postgres
FINANCE_DB_HOST=127.0.0.1
FINANCE_DB_PORT=5432
FINANCE_DB_NAME=youjulab_finresearch
FINANCE_DB_USER=your_database_role
FINANCE_DB_PASSWORD=your_database_password
ACCOUNT_JWT_SECRET=your_persistent_random_secret_at_least_32_characters
MCP_ACCESS_TOKEN=your_persistent_random_service_token
EMBEDDING_BASE_URL=http://127.0.0.1:11434
EMBEDDING_MODEL=bge-m3
EMBEDDING_DIM=1024
```

Do not distribute this `.env` to desktop users. Keep JWT signing secrets stable across
restarts. PostgreSQL need only listen for the MCP server, not desktop clients.
For access beyond a trusted LAN, terminate HTTPS at a reverse proxy; HTTP sends credentials
and document text without transport encryption. Limit the MCP port with a firewall.
The existing JWT-authenticated portfolio/news endpoints derive owners from verified tokens,
not client-supplied owner headers. Legacy static-token tools remain administrator/service tools.

Manual startup from the repository root:

```sh
.venv/bin/python mcp-server/server.py
```

Ollama or another embedding provider is still deployed and started separately on the server.
Desktop startup and shutdown never start or stop this shared service.

## SQLite Migration

Create a separate EMPTY PostgreSQL database first. Do not replace an existing financial
database: it may contain accounts or portfolios that do not exist in the local SQLite source.
Configure `FINANCE_DB_*` / `FINANCE_DATABASE_URL` to point at that new database.
Stop source writers before the final cutover; old running processes retain their old config.

From `mcp-server`, inspect first:

```sh
../.venv/bin/python -m db.migrate_sqlite_to_postgres \
  --source '/path/to/userData/finresearch.sqlite' \
  --backup-dir '/path/to/userData/migration-backups'
```

Add `--apply` to execute. The tool creates a consistent SQLite backup, validates integrity
and foreign keys, copies all supported tables in one PostgreSQL transaction, compares row
counts and resets serial sequences. Unknown source tables or nonempty targets abort rather
than silently lose/overwrite records. Store the generated `migration.json` with the snapshot.
PDFs and vector files do not need migration and must stay local.

Re-login after moving from a desktop-specific JWT signing key to the shared server key.
Passwords remain valid because migration preserves their hashes. The original SQLite files
and older PostgreSQL databases remain available for rollback; never run both as active writers.

## Backups

Back up PostgreSQL on the server with a scheduled `pg_dump` and test restoration separately.
Desktop backups export only the signed-in user's Office rows, vectors and PDFs into an isolated
archive. Shared SQLite files are never packaged. Restore merges only that user back into the
local stores, preserving other users and all current login credentials. Old full-database
archives remain on disk but are not eligible for user-scoped restore. Server accounts,
passwords and portfolio transactions are never restored by the desktop.

Application version management and cross-device Office synchronization are outside this change.
