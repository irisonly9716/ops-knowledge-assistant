# AGENTS.md — Operations Knowledge Assistant

<!--
  Shared rules for every AI tool on this repo. Claude Code reads CLAUDE.md, which contains only "@AGENTS.md".
  Keep this file under 200 lines, and add a changelog line with every change.
-->

## 0. Before you start

<!-- A fixed marker shows at a glance whether the agent actually loaded this file. -->
- Start every reply with 😊.
- Reply to Arianna in Chinese. Everything in the repository is in English: code, comments, docs, commit messages, issues, sample data.
- Read the GitHub issue you are working on and restate what "done" means before writing code.
- Discuss design changes before implementing them. Ask about decisions that are Arianna's to make; for plumbing, run it yourself and report the result with your analysis.
- When you report a change, say why it was made this way and which alternative wasn't taken.

## 1. Project context

An assistant for live-commerce operations staff. One entry point answers SOP and guideline questions (RAG), historical campaign data questions (Text-to-SQL), or both, with routing, multi-turn memory and department-level permissions.

**Principle:** the project analyzes content and results with AI; it does not recompute analytics. Clean numbers go in SQL columns, narrative (SOPs, plans, reviews) goes through RAG, and data other teams already computed is uploaded, not calculated here.

**Status:** upload, approval and ingestion are implemented. `/query`, retrieval, reranking, Text-to-SQL, routing, cache and memory are not built yet (`app/sql_agent`, `app/router`, `app/memory` are empty packages).

**Language:** documents and questions are in English. There is no cross-language retrieval.

### Scale

Design for these requirements, agreed with the CEO and department stakeholders from the workload of peak sale events such as Black Friday. The development environment and its mock data (4 departments, 25 users, invented documents) don't represent real volume: never size anything from them.

| | Current requirement | Next optimization target |
| --- | --- | --- |
| Users | 8 departments, 500 people | |
| Peak query load | 300 QPS | 500 QPS |
| Latency per query | 3 s | 3 s |
| Queries in flight (QPS × latency) | 900 | 1,500 |

- **Rate limit:** designed for the 300 QPS peak; above it, `429` with `Retry-After`.
- **Model concurrency:** sized for the peak (900 across all instances) and configurable. Development sets a low value in `.env` because those tests are paid for privately; that value is never a production default or evidence for sizing.
- **Reranking:** only sees chunks that passed the department filter and counts toward the 3 s; on a timeout or an error, fall back to vector order. It is on by default unless the eval shows no gain or it breaks the 3 s budget: then it stays in the code, is switched off in config, and the numbers go in the report.
- **Not load tested yet (#11).** Until it is, write "designed for 300 QPS", not "handles 300 QPS".
- **Upload path:** 1 partition, 1 consumer, 1 broker and local files, because the volume wasn't known when it was designed; #10 is the way out. Partitions follow upload volume: queries don't pass through Kafka.

### Tech stack

<!-- Checked against requirements.txt, pom.xml and docker-compose.yml on 2026-11-02. Those files are the source of truth. -->

| Area | Choice | Version |
| --- | --- | --- |
| Backend | Java · Maven wrapper · Spring Boot (webmvc, data-jpa, kafka, actuator) · Lombok and the PostgreSQL driver via Boot | 25 · 3.9.16 · 4.1.1 |
| LLM service | Python via `uv` · FastAPI · uvicorn · pydantic-settings · python-dotenv | 3.12 · 0.115.6 · 0.34.0 · 2.7.1 · 1.0.1 |
| Database | `pgvector/pgvector` · Flyway · psycopg (binary) | pg16 · 13.10.3-alpine · 3.2.3 |
| Messaging | `apache/kafka` (KRaft) · kafka-ui · confluent-kafka | 3.9.0 · v0.7.2 · 2.6.1 |
| Cache and memory | Redis (running, not wired in yet) | 7-alpine |
| Models | openai SDK · `text-embedding-3-small` (1536 dimensions) · default chat model `gpt-6-luna` | 3.29.0 |
| Parsing | markdown-it-py · python-docx · pdfplumber · pyyaml | 4.2.0 · 1.2.0 · 0.11.10 · 6.0.3 |
| Chunking | langchain-text-splitters · tiktoken | 1.1.3 · 0.14.0 |
| Tests and mock data | pytest · httpx · faker · JUnit and Mockito via Boot | 9.1.1 · 0.28.1 · 33.1.0 |

- Planned, not installed: LangGraph, sqlglot, a reranker (Cohere Rerank or `bge-reranker-v2-m3`, chosen by eval), and pandas with openpyxl (#9).
- Never upgrade a major version inside a feature change.
- Your training data may be older than these versions. Spring Boot 4 uses Jackson 3 (`tools.jackson.*`; annotations stay in `com.fasterxml.jackson.annotation`), `@MockitoBean`, and `@WebMvcTest` in `org.springframework.boot.webmvc.test.autoconfigure`. Check the docs for the installed version.

### Commands

| Task | Command |
| --- | --- |
| Start Postgres, Flyway, Redis, Kafka | `docker compose up -d` (repo root) |
| Apply migrations | `docker compose run --rm flyway migrate` |
| Python setup | `cd llm-service` · `uv venv` · `uv pip install -r requirements.txt` |
| Create topics · mock data | `uv run python -m scripts.init_topics` · `uv run python -m scripts.generate_mock_data` |
| Run backend (:8081) | `cd backend-service` · `.\mvnw.cmd spring-boot:run` |
| Run consumer · LLM API (:8000) | `uv run python -m app.consumers.upload_consumer` · `uv run uvicorn app.main:app --reload --port 8000` |
| Python tests | `uv run pytest` (database tests use a throwaway database, skipped if Postgres is down) |
| Java tests: unit and slice · everything | `.\mvnw.cmd test` · `.\mvnw.cmd test "-DexcludedGroups=none"` (needs the compose Postgres) |

- `generate_mock_data` empties uploads, documents and chunks through foreign keys. After `docker compose down -v`, rerun topics and mock data.
- A database authentication error usually means the wrong port: check `POSTGRES_PORT` first, because a native Postgres may hold 5432.
- In Git Bash, put `MSYS_NO_PATHCONV=1` before `docker exec ... /opt/...`. Never choose "Reset to factory defaults" in Docker Desktop: it wipes the volumes.

### Architecture

```
backend-service/       Spring Boot: REST API, permissions, approval, outbox relay
  upload/              endpoints, UploadService, UploadPolicy (the rules as plain functions), UploadStorage
  outbox/              OutboxRelay, OutboxScheduler
  user/                CurrentUserArgumentResolver: the only reader of X-User-Id
  campaign/ department/ document/ config/ web/
llm-service/app/       Python: ingestion now, query path later
  consumers/           consume loop, handlers (documents), data_tables (CSV), storage, events
  rag/                 parsing/, chunking, document_fields, embeddings, store
  time_budget.py       one time budget per message
contracts/             upload-event.examples.json, validated by both test suites
db/migration/          Flyway migrations, V1 onwards
sop_docs/ campaign_docs/ data_tables/    sample uploads
docs/                  architecture, data-schema, permission-model, development
```

Key patterns:
- **Upload:** one transaction writes the `uploads` row and its `outbox` row; the relay publishes afterwards (`FOR UPDATE SKIP LOCKED`). No request waits for Kafka. An upload waiting for approval has no event.
- **Consumer:** at least once, so idempotent on `uploads.status`. Three steps: short transaction to claim; no transaction for slow work; short transaction to write, checking the status again under the lock.
- **Errors:** a `PermanentError` goes straight to the dead-letter topic (retrying gives the same answer); anything else is retried within the time budget. A failed upload records why, naming the row and column for a bad CSV.
- **Time budget:** every layer shares one budget per message, with SDK retries off. Compute the worst case from every timeout and retry, not just the typical case.
- **Replacements:** optimistic concurrency. An upload names the version it replaces (`replaces_upload_id`) and is void if that is no longer current. `doc_id` stays stable, `version` goes up.
- **Data tables:** validate every cell, then replace all of a campaign's rows in one transaction with `COPY`, so `statement_timeout` bounds the load.
- **HTTP errors:** always `{"error": "..."}`. 401 unknown user, 400 malformed, 403 not yours to touch, 404 unknown id, 409 fine but conflicts with what exists, 413 file too large.
- **Message contract:** change `contracts/upload-event.examples.json` first; `storageKey` is a logical key, never a path.
- **Permissions:** `department_id` is in every schema and query change. The department comes from the `users` table, never from the request. Rules: [docs/permission-model.md](docs/permission-model.md).
- **ROI:** `SUM(campaign_products.profit) / campaigns.actual_operating_cost`. Profit already nets the cost of goods.

### Naming and code style

- Write code that reads like the code around it: its comment density, naming and idiom.
- Java: PascalCase classes, camelCase members; a package per domain. Python: snake_case, type hints, frozen dataclasses for records.
- Database: snake_case, plural tables, `timestamptz` named `created_at` / `updated_at`. JSON on the wire is camelCase; enum values are lowercase.
- Comments say why, including measurements behind a decision. They describe the system, never the order it was built in or any schedule.
- SQL parameters only, never concatenated values. A table name may come only from an enum or a constant, never from a request or a message.

### Testing strategy

- Name tests as sentences about behavior: `a_new_sop_for_all_departments_waits_and_publishes_nothing`, `test_a_bad_csv_is_a_permanent_error_and_the_current_rows_stay`.
- **Python:** fake embedder and fake Kafka for the logic; a throwaway Postgres database for SQL, never development data.
- **Java:** `@WebMvcTest` plus the real `UploadService` with fake repositories, so status codes go through the rules. `@Tag("integration")` tests use the real Postgres and only touch rows they create.
- **Mutation checks:** put each bug back on purpose and watch its test fail. First prove the baseline passes, and count only a named failing test; a compile error or a tool that didn't run proves nothing.
- **End to end:** anything crossing a process needs a real run with the backend, Kafka, consumer and Postgres; slice tests can't see Tomcat or the network.
- **Measure before deciding** (size, time, memory), compare alternatives fairly, and report worst cases.
- Never weaken, skip or delete a test to make it pass. If a test looks wrong, say so.

## 2. References

- [docs/architecture.md](docs/architecture.md): pipelines, outbox, query flow, measured limits.
- [docs/data-schema.md](docs/data-schema.md): tables and why they look this way. [docs/permission-model.md](docs/permission-model.md): roles and approval.
- [docs/development.md](docs/development.md): local setup. [backend-service/README.md](backend-service/README.md) and [llm-service/README.md](llm-service/README.md): API, consumer behavior, tests.

Roles, enforced in code and the database, not only by hiding things:

| Role | Sees | Uploads | Approves |
| --- | --- | --- | --- |
| staff | own department + ALL | yes | no |
| manager | own department + ALL | yes | staff of their own department |
| executive | everything | never | managers |
| admin | everything + system data | never | never; system maintenance only |

A new document for one's own department takes effect at once. Replacing anything, or anything for ALL, needs the uploader's superior.

## 3. Git workflow

- One branch per change, from up-to-date `master` or stacked on the branch it needs; descriptive names such as `upload-permissions`.
- Commits: `type: summary` (`feat`, `fix`, `docs`, `test`, `chore`), imperative, with a body explaining why. A commit made by an agent ends with that agent's `Co-Authored-By` line; a PR body ends with its generated-with line.
- Reference issues with `Closes #N` in the commit or PR that finishes them.
- Merge with a merge commit, never squash. Never enable auto-merge unless asked.
- **Never push, open or merge a PR, deploy or spend cloud money without Arianna's approval for that action.** Arianna usually pushes through GitHub Desktop.
- Found something out of scope? Say so, and open an issue only when asked. Work not built yet goes into the plan; work for after the product is done goes into an issue.
- Schema changes are new migration files. Never edit an applied migration.

## 4. Do's and don'ts

**Do**
- Fix the root cause, in the code and the docs together.
- Keep docs true to the code: state only what is implemented or measured, and mark requirements as requirements.
- Reuse what exists: `UploadPolicy`, `CurrentUser`, `TimeBudget`, the outbox, `db.connect()` with its bounded waits.
- Before adding a dependency, measure what it costs, explain why it is needed and wait for approval.

**Don't**
- Don't read `X-User-Id` outside `CurrentUserArgumentResolver`, or trust a department, role or user id from a request.
- Don't call Kafka from a request, or hold a database lock across a network call.
- Don't add unbounded waits or hidden retries: every timeout and retry goes into the worst case.
- Don't use floats for money or amounts in the database path: columns are `NUMERIC`, Python uses `Decimal`.
- Don't stop or uninstall a native Postgres service on a developer's machine.

**Security**
- Never read, print or commit `.env` or secrets such as `OPENAI_API_KEY`. `.env.example` has placeholders only, and secrets never go into images.
- `X-User-Id` is simulated identity: anyone can forge it (#5). State that accurately; the authorization rules are real, the authentication isn't yet.
- Client file names are hostile: `UploadStorage.sanitizeFilename` and `resolve_upload_path` defend against path traversal. Keep both.
- Generated SQL gets the planned layers: `SELECT` only, parser checks, a read-only role and row-level security. No single layer is enough.

## Changelog

- v1 · 2026-11-02 · Arianna · First draft: reply marker, dependency versions, scale requirements.
- v2 · 2026-11-03 · Arianna · Reranking rule and English-only language premise.
