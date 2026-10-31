# Local development

## Prerequisites
- **Docker Desktop** (on Windows, with the WSL 2 based engine).
- **JDK 25**. Maven is not required; the project ships the Maven wrapper.
- **Python 3.12** with [uv](https://docs.astral.sh/uv/).

## Run it end to end (order matters)

```bash
# 1. Infrastructure. Set POSTGRES_PASSWORD in .env first (there is no default).
#    `up` also runs the flyway service once, which applies the schema migrations and exits.
cp .env.example .env
docker compose up -d
docker compose ps                     # wait until postgres / redis / kafka are healthy
docker compose run --rm flyway info   # every migration should be "Success"

# 2. Python environment and Kafka topics. Must run before any upload:
#    topic auto-creation is disabled, so uploads return 503 until the topics exist.
cd llm-service
uv venv
uv pip install -r requirements.txt
uv run python -m scripts.init_topics

# 3. Sample data. Must run before any upload: the API checks that the
#    department / campaignId exist in the database and returns 400 otherwise.
uv run python -m scripts.generate_mock_data

# 4. Backend (new terminal, from the repo root)
cd backend-service
./mvnw spring-boot:run                # Windows: .\mvnw.cmd spring-boot:run

# 5. Consumer (another terminal, from the repo root): a standalone worker process, not part of FastAPI
cd llm-service
uv run python -m app.consumers.upload_consumer

# 6. Upload a document (third terminal, from the repo root). X-User-Id names the uploader (any user_id from the
#    users table; 2 is a staff member in the generated data). The consumer log shows the document being
#    parsed, chunked, embedded (needs OPENAI_API_KEY in .env) and stored.
#    In PowerShell, type curl.exe instead of curl (plain `curl` is an alias for a different command there).
curl -X POST http://localhost:8081/documents/upload -H "X-User-Id: 2" -F "file=@sop_docs/account-restriction-handling.md" -F "contentKind=document" -F "docType=sop" -F "department=ALL" -F "title=Account Restriction Handling"
```

`docker compose down -v` deletes all volumes (Postgres data and Kafka topics). The next `up` rebuilds the schema from the migrations; run steps 2 and 3 again afterwards.

## Schema changes

The schema lives in versioned SQL files under `db/migration/` (`V1__...sql`, `V2__...sql`, ...), applied in version order by [Flyway](https://documentation.red-gate.com/flyway). Flyway records each applied file, with a checksum, in the `flyway_schema_history` table and only runs new ones.

- To change the schema, add a new file with the next version number, e.g. `V4__add_query_audit.sql`, then run `docker compose run --rm flyway migrate` (or just `docker compose up -d`).
- **Never edit a migration that has already been applied.** Its checksum would no longer match, and the next `migrate` stops with a validation error. Fix mistakes with a new migration instead.
- Each file runs in one transaction: if any statement fails, the whole file is rolled back.
- The Spring backend does not create or change tables (`ddl-auto: validate`); it only checks at startup that its entities match the schema.

## Tests

```bash
# Java: Docker not required (run from the repo root)
cd backend-service
./mvnw test                           # Windows: .\mvnw.cmd test

# Python: Kafka not required. tests/test_ingestion.py needs the docker compose Postgres (it creates and
# drops a throwaway database) and is skipped when Postgres isn't running
cd ../llm-service
uv run pytest
```

## Verify the infrastructure

```bash
docker compose exec postgres psql -U ops -d ops_assistant -c "SELECT extname FROM pg_extension;"   # should list vector
docker compose run --rm flyway info                                                                # migration history
docker compose exec redis redis-cli ping                                                           # PONG
docker compose exec kafka /opt/kafka/bin/kafka-topics.sh --bootstrap-server localhost:9092 --list
```

Kafka UI is available at http://localhost:8080.

## Troubleshooting

**`password authentication failed for user "ops"` although `.env` looks right**
- Another PostgreSQL may be listening on port 5432 (for example a native Windows install). Check with `Get-NetTCPConnection -LocalPort 5432 -State Listen` in PowerShell. Set `POSTGRES_PORT=5433` in `.env` and run `docker compose up -d`; Compose, Spring and Python all read this variable.
- Postgres only uses `POSTGRES_PASSWORD` when the data volume is first created. Changing it in `.env` later does not change the database password: run `ALTER USER` inside the container, or recreate the volume with `docker compose down -v`.

**`ClassFormatError` on startup, or Lombok-annotated classes flagged in VS Code**
VS Code's Java extension compiles with the Eclipse compiler and can write broken classes into `target/classes`; Maven then skips recompiling. Run `./mvnw clean`, and in VS Code run `Java: Clean Java Language Server Workspace`. The project targets Java 25 because at language level 21 that compiler rejects Lombok-generated constructors. If both the Red Hat and the Oracle Java extensions are installed, disable one of them for this workspace.

**Flyway: `Found non-empty schema(s) "public" but no schema history table`**
The database was created before the project used Flyway (its tables came from Postgres' one-time init scripts), so Flyway does not know which migrations it already contains. Flyway refuses to guess. Tell it once that the existing tables correspond to version 2, then migrate:
```bash
docker compose run --rm flyway baseline -baselineVersion=2
docker compose run --rm flyway migrate
```
If the data is disposable, `docker compose down -v` and a fresh `up` work as well.

**Flyway: `Validate failed: Migrations have failed validation` / checksum mismatch**
An already-applied migration file was edited. Restore the original file (for example with `git checkout -- db/migration/`) and put the change in a new migration.

**The consumer picks up nothing after a long break, or replays old messages**
Kafka forgets a consumer group's committed offsets after 7 days without activity. With
`auto.offset.reset=earliest` the consumer then starts from the oldest message still in the topic. Messages
for uploads that are already `done` are skipped; messages without an uploads row go to the dead-letter topic.

**`No module named 'scripts'`**
Run the Python scripts from inside `llm-service/`, not from the repository root.
