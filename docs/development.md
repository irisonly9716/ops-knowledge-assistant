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

# 2. Python environment and Kafka topics. Must run before uploads can be processed: topic auto-creation is
#    disabled, so until the topics exist uploads are accepted but their events wait in the outbox.
cd llm-service
uv venv
uv pip install -r requirements.txt
uv run python -m scripts.init_topics

# 3. Sample data. Must run before any upload: the API looks up the uploader (401 if unknown)
#    and checks that the campaignId exists (400 otherwise).
uv run python -m scripts.generate_mock_data

# 3b. The example questions of the Text-to-SQL pipeline, with their embeddings (needs OPENAI_API_KEY; tens of short
#     questions, a tiny fraction of a cent). Run it again after editing app/sql_agent/examples.yaml
uv run python -m scripts.load_sql_examples

# 3c. The login of sql_reader, the role generated SQL runs as. Set SQL_READER_PASSWORD in .env first; run it again
#     after changing the password. Needed by /query, whose data path runs as it, and by evaluation.sql_run --pipeline --views
uv run python -m scripts.enable_sql_reader

# 4. Backend (new terminal, from the repo root)
cd backend-service
./mvnw spring-boot:run                # Windows: .\mvnw.cmd spring-boot:run

# 4b. The API for questions (another terminal). Needs OPENAI_API_KEY, INTERNAL_SERVICE_TOKEN and SQL_READER_PASSWORD
#     in .env (and step 3c run): it refuses to start without them. The backend reads the same token, so restart it if you
#     only just added it. See .env.example for how to make one.
cd llm-service
uv run python -m app.server --reload

# 5. Consumer (another terminal, from the repo root): a standalone worker process, not part of FastAPI
cd llm-service
uv run python -m app.consumers.upload_consumer

# 6. Upload a document (third terminal, from the repo root). X-User-Id names the uploader (any user_id from the
#    users table; 2 is a staff member in the generated data). The consumer log shows the document being
#    parsed, chunked, embedded (needs OPENAI_API_KEY in .env) and stored.
#    In PowerShell, type curl.exe instead of curl (plain `curl` is an alias for a different command there).
#    scope=department takes effect at once; scope=all would wait for approval by the manager (user 1).
curl -X POST http://localhost:8081/documents/upload -H "X-User-Id: 2" -F "file=@sop_docs/account-restriction-handling.md" -F "contentKind=document" -F "docType=sop" -F "mode=new" -F "scope=department" -F "title=Account Restriction Handling"

# 6b. Or load the whole evaluation corpus (85 documents) through the API, approvals included. The consumer
#     embeds about 51,000 tokens, well under a cent
cd llm-service
uv run python -m scripts.ingest_corpus

# 7. Upload a data table: a CSV for campaign 11, which is still running and so has no rows yet
curl -X POST http://localhost:8081/documents/upload -H "X-User-Id: 2" -F "file=@data_tables/beauty-weekly-live-special-products.csv" -F "contentKind=data_table" -F "tableType=campaign_products" -F "mode=new" -F "campaignId=11"

# 8. Ask a question. The answer cites what it used; a question the documents don't cover gets a refusal
curl -X POST http://localhost:8081/query -H "X-User-Id: 2" -H "Content-Type: application/json" -d '{"question": "How do I handle an account restriction?"}'
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

# Python: Kafka not required. tests/test_ingestion.py and tests/test_retrieval.py need the docker compose Postgres
# (they create and drop a throwaway database) and are skipped when Postgres isn't running
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

## The reranker (optional)

```bash
docker compose --profile rerank up -d reranker   # Text Embeddings Inference with bge-reranker-v2-m3, on :8082
curl -s localhost:8082/rerank -H "Content-Type: application/json" -d '{"query": "refund", "texts": ["Refunds take 5 days.", "Hosts rotate weekly."]}'
```

The first start downloads the model (2.2 GB) into the `reranker_models` volume; the container needs about 5 GB of
memory. It isn't started by `docker compose up -d`, and nothing needs it: llm-service keeps the vector order when it is
missing or slow, and logs a warning on each query. Set `RERANKER_URL=` (empty) in `.env` to turn reranking off and the
warnings with it. On a laptop CPU one query's rerank takes about 40 s, so `/query` always falls back; the evaluation
waits for it with `--rerank-timeout`.

If the container exits with code 137 while "Warming up model", Docker ran out of memory: give Docker Desktop more, or
stop containers you don't need. `--max-batch-tokens 1024` in `docker-compose.yml` is what made it fit in 8 GB.

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

**An upload stays `received` and the consumer never sees it**
Its event is probably still in the outbox. `SELECT id, message_key, attempts, last_error FROM outbox WHERE published_at IS NULL;`
shows what is waiting and why the last publish failed (typically Kafka not running, or the topic not created:
run `init_topics`). The backend's relay retries on its own, backing off up to 30 seconds; nothing needs to be
re-uploaded.

**`No module named 'scripts'`**
Run the Python scripts from inside `llm-service/`, not from the repository root.
