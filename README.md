# Operations Knowledge Assistant

A chat assistant for **live-streaming e-commerce operations teams**. One entry point answers two kinds of questions, or both at once:

| Question type | Example | Pipeline |
|---|---|---|
| **SOPs / guidelines** (unstructured) | "A stream got flagged by risk control. What's the first step, what's the last step, and which team do we loop in?" | RAG: chunk documents → embed → vector search → LLM summary |
| **Historical data** (structured) | "What were the GMV and conversion rate of the last similar campaign?" | Text-to-SQL: natural language → SQL → query → LLM explanation |
| **Both** | "How should we run the new campaign? Use comparable past campaigns as a reference." | Router → run both pipelines → merged answer |

On top of that: **multi-turn memory** (follow-up questions keep context) and **department-level access control** (each user only sees their own department's data and documents).

---

## The problem

Ops staff look up past campaign and anchor (streamer) results by digging through spreadsheets, and there is no ready playbook when an incident such as a risk-control flag happens. This project turns past campaign data, anchor data and SOP documents into a single conversational knowledge base.

**Design principle: use AI to analyze content and results, not to rebuild an analytics engine.**
- Clean metrics (GMV, viewers, conversion rate, …) live in SQL tables and are queried via Text-to-SQL.
- Narrative and judgment (campaign plans, post-mortems, ROI explanations) live in documents and are retrieved via RAG.
- Data other teams already compute (product sales, customer behavior) is ingested as-is, never recomputed here.

---

## Status

**Implemented**
- Local infrastructure via `docker-compose`: PostgreSQL 16 + pgvector, Redis, Kafka (KRaft mode), Kafka UI.
- Database schema (departments, users, anchors, campaigns, metrics, product sales, customer behavior, documents, vector chunks with an HNSW index) plus a mock-data generator.
- **Upload API** (Spring Boot): validates input against enums and against the database, stores the file, and publishes an event to Kafka. Explicit failure semantics: returns 503 within seconds when Kafka is unavailable, and deletes the stored file only when the send failure is definitive (a timeout is treated as "unknown", not as "failed").
- **Document-ingestion consumer** (Python): at-least-once delivery with manual offset commits, exponential-backoff retries, a dead-letter topic, graceful shutdown, and path-traversal protection.
- A **cross-service message contract** ([contracts/](contracts/)) that both the Java and the Python test suites validate against, so neither side can silently change the message format.
- Tests: 39 Java (JUnit / MockMvc) and 54 Python (pytest), none of which need Kafka running.

**In progress / planned**
- Document parsing, chunking, embeddings, storage in pgvector, and retrieval.
- `/query` endpoint: RAG answers, Text-to-SQL (read-only, with row-level filtering), and query routing.
- Redis query cache and conversation memory.
- Department-level permission enforcement in both vector retrieval and SQL.
- Containerized deployment.

---

## Architecture (target)

```
                          ┌──────────────────────────────┐
   User question ────────▶│       backend-service        │  Spring Boot (Java)
   (X-User-Id header)     │  - REST API (/query, ...)    │
                          │  - file upload → Kafka       │
                          │  - structured data (JPA)     │
                          └──────────────┬───────────────┘
                                         │ Kafka: document-upload-events
                                         │ HTTP: forwards /query
                                         ▼
                          ┌──────────────────────────────┐
                          │         llm-service          │  Python (FastAPI)
                          │  - Kafka consumer (ingest)   │
                          │  - RAG: chunk/embed/retrieve │
                          │  - Text-to-SQL agent         │
                          │  - query router + memory     │
                          └──────┬────────────┬──────────┘
                                 │            │
              ┌──────────────────┘            └──────────────────┐
              ▼                                                  ▼
   ┌──────────────────────┐                         ┌──────────────────────┐
   │ PostgreSQL + pgvector│                         │        Redis         │
   │ - business tables    │                         │ - query cache        │
   │ - vector chunks      │                         │ - session memory     │
   └──────────────────────┘                         └──────────────────────┘

   Kafka (KRaft) decouples file upload from document processing.
```

### Ingestion flow (implemented)

```
client ─HTTP─▶ backend (Java)                         Kafka                       consumer (Python)
               1. sanitize the client-supplied name                               4. poll a message
               2. save to UPLOAD_DIR/<uploadId>-<name>                            5. parse + validate contract
               3. publish {uploadId, storageKey, ...} ──▶ document-upload-events ─▶ 6. storageKey → real path (traversal-safe)
                                                                                  7. read the file (parsing/embedding next)
                                                  document-upload-events.dlq ◀── 8. success → commit; failure → retry → DLQ
```

The message carries a **storage key** (a logical name), not a file path: each service resolves it against its own `UPLOAD_DIR`, so the contract does not depend on the sender's working directory, operating system or machine.

---

## Repository layout

```
ops-knowledge-assistant/
├── docker-compose.yml      # Postgres+pgvector / Redis / Kafka / Kafka UI
├── .env.example            # environment template (copy to .env)
├── sql/
│   ├── 01_init.sql         # extensions (pgvector)
│   └── 02_schema.sql       # full schema
├── backend-service/        # Spring Boot (Java): REST API, upload, Kafka producer
├── llm-service/            # Python: Kafka consumer, RAG, Text-to-SQL, routing
├── contracts/              # cross-service message examples (validated by both test suites)
├── sop_docs/               # sample SOP documents (Markdown)
├── campaign_docs/          # sample campaign plans / post-mortems (Markdown)
├── uploads/                # uploaded files at runtime (git-ignored)
└── docs/
    ├── architecture.md     # architecture and runtime flows
    ├── data-schema.md      # table design, including users and permission fields
    └── permission-model.md # department-level access model
```

The sample documents and the design docs under `docs/` are written in Chinese, since the target users are Chinese-speaking operations teams.

---

## Getting started

### Prerequisites
- **Docker Desktop** (on Windows, with the WSL 2 based engine).
- **JDK 25**. Maven is not required; the project ships the Maven wrapper.
- **Python 3.12** with [uv](https://docs.astral.sh/uv/).

### Run it end to end (order matters)

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

# 3. Mock data. Must run before any upload: the API checks that the
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

### Tests

```bash
# Java: 39 tests, Docker not required (run from the repo root)
cd backend-service
./mvnw test                           # Windows: .\mvnw.cmd test

# Python: 54 tests, Kafka not required
cd ../llm-service
uv run pytest
```

`docker compose down -v` deletes all volumes (Postgres data and Kafka topics); run steps 2 and 3 again afterwards.

### Verify the infrastructure

```bash
docker compose exec postgres psql -U ops -d ops_assistant -c "SELECT extname FROM pg_extension;"   # should list vector
docker compose exec redis redis-cli ping                                                           # PONG
docker compose exec kafka /opt/kafka/bin/kafka-topics.sh --bootstrap-server localhost:9092 --list
```

Kafka UI is available at http://localhost:8080.

### Troubleshooting

**`password authentication failed for user "ops"` although `.env` looks right**
- Another PostgreSQL may be listening on port 5432 (for example a native Windows install). Check with `Get-NetTCPConnection -LocalPort 5432 -State Listen` in PowerShell. Set `POSTGRES_PORT=5433` in `.env` and run `docker compose up -d`; Compose, Spring and Python all read this variable.
- Postgres only uses `POSTGRES_PASSWORD` when the data volume is first created. Changing it in `.env` later does not change the database password: run `ALTER USER` inside the container, or recreate the volume with `docker compose down -v`.

**`ClassFormatError` on startup, or Lombok-annotated classes flagged in VS Code**
VS Code's Java extension compiles with the Eclipse compiler and can write broken classes into `target/classes`; Maven then skips recompiling. Run `./mvnw clean`, and in VS Code run `Java: Clean Java Language Server Workspace`. The project targets Java 25 because at language level 21 that compiler rejects Lombok-generated constructors. If both the Red Hat and the Oracle Java extensions are installed, disable one of them for this workspace.

**`No module named 'scripts'`**
Run the Python scripts from inside `llm-service/`, not from the repository root.

---

## Tech choices

| Component | Role | Why |
|---|---|---|
| Kafka | Async queue between upload and document processing | Parsing and embedding are slow; the upload API must not block on them. Decoupling lets the processing side retry and scale independently. |
| pgvector | Vector storage and similarity search | One PostgreSQL instance holds both business tables and vectors, so there is no separate vector database to operate. |
| Redis | Query cache and session memory | Repeated questions skip the LLM; conversation history needs fast reads and writes. |
| Spring Boot | Public API, uploads, structured data | Mature Java stack; JPA for the business tables. |
| Python (FastAPI) | LLM, embedding and agent logic | The AI ecosystem (LangChain, LlamaIndex, sentence-transformers) is richest in Python. |

---

## Further reading

- [docs/architecture.md](docs/architecture.md): runtime flows, delivery semantics, dead-letter handling
- [docs/data-schema.md](docs/data-schema.md): table design
- [docs/permission-model.md](docs/permission-model.md): department-level access model
- [backend-service/README.md](backend-service/README.md) and [llm-service/README.md](llm-service/README.md): per-service details
