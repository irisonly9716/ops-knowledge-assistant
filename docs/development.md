# Local development

## Prerequisites
- **Docker Desktop** (on Windows, with the WSL 2 based engine).
- **JDK 25**. Maven is not required; the project ships the Maven wrapper.
- **Python 3.12** with [uv](https://docs.astral.sh/uv/).

## Run it end to end (order matters)

```bash
# 1. Infrastructure. Set POSTGRES_PASSWORD in .env first (there is no default).
cp .env.example .env
docker compose up -d
docker compose ps                     # wait until postgres / redis / kafka are healthy

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

# 6. Upload a document (third terminal, from the repo root); the consumer log should show the file being read.
#    In PowerShell, type curl.exe instead of curl (plain `curl` is an alias for a different command there).
curl -X POST http://localhost:8081/documents/upload -F "file=@sop_docs/风控问题处理流程.md" -F "contentKind=document" -F "docType=sop" -F "department=ALL"
```

`docker compose down -v` deletes all volumes (Postgres data and Kafka topics); run steps 2 and 3 again afterwards.

## Tests

```bash
# Java: Docker not required (run from the repo root)
cd backend-service
./mvnw test                           # Windows: .\mvnw.cmd test

# Python: Kafka not required
cd ../llm-service
uv run pytest
```

## Verify the infrastructure

```bash
docker compose exec postgres psql -U ops -d ops_assistant -c "SELECT extname FROM pg_extension;"   # should list vector
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

**`No module named 'scripts'`**
Run the Python scripts from inside `llm-service/`, not from the repository root.
