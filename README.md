# Operations Knowledge Assistant

An AI assistant that gives **live-streaming e-commerce operations teams** one place to ask about their SOPs and their historical campaign performance, with every answer scoped to the asking user's department.

---

## Overview

Operations staff answer two kinds of questions every day. The assistant handles both through a single chat entry point:

| Question type | Example | How it is answered |
|---|---|---|
| **SOPs and guidelines** (unstructured) | "A stream got flagged by risk control. What's the first step, what's the last step, and which team do we loop in?" | **RAG**: documents are chunked and embedded; the question is matched against them by vector search; an LLM writes the answer from the retrieved passages |
| **Historical data** (structured) | "What were the GMV and conversion rate of the last similar campaign?" | **Text-to-SQL**: an LLM turns the question into a read-only SQL query, the query runs, and the LLM explains the result |
| **Both** | "How should we run the new campaign? Use comparable past campaigns as a reference." | A **router** classifies the question, runs both pipelines, and merges the results into one answer |

On top of that, conversations have **multi-turn memory**, and **department-level access control** ensures each user only sees their own department's documents and data.

**Design principle: use AI to analyze content and results, not to rebuild an analytics engine.**
- Clean metrics (GMV, viewers, conversion rate, costs) live in SQL tables and are answered through Text-to-SQL.
- Narrative and judgment (campaign plans, post-mortems, why a campaign over- or under-performed) live in documents and are answered through RAG.
- Data that other teams already compute (product sales, customer behavior) is ingested as-is and never recomputed.

---

## User stories

- As an **operations specialist** whose live stream was just flagged by the platform's risk control, I want to ask what to do and get the steps and the teams to contact from our SOPs, so that I can react in minutes instead of searching through documents.
- As a **campaign manager**, I want to ask for the GMV and conversion rate of a past campaign in plain language, so that I don't have to dig through spreadsheets or write SQL.
- As an **operations lead planning a new campaign**, I want recommendations that combine our playbooks with the results of comparable past campaigns (anchors, products, costs, ROI), so that the plan is grounded in what actually worked.
- As **any user**, I want to ask follow-up questions without repeating the context, so that the conversation feels natural.
- As an **operations specialist**, I want to upload SOPs, campaign plans, post-mortems and data exports such as product sales and customer behavior, so that the assistant's knowledge stays current.
- As a **department head**, I want each employee to see only their own department's documents and data (company-wide SOPs excepted), so that sensitive campaign numbers don't leak across teams.

---

## System architecture

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

### Ingestion flow

```
client ─HTTP─▶ backend (Java)                         Kafka                       consumer (Python)
               1. sanitize the client-supplied name                               4. poll a message
               2. save to UPLOAD_DIR/<uploadId>-<name>                            5. parse + validate the contract
               3. publish {uploadId, storageKey, ...} ──▶ document-upload-events ─▶ 6. storageKey → real path (traversal-safe)
                                                                                  7. parse / chunk / embed / store
                                                  document-upload-events.dlq ◀── 8. success → commit; failure → retry → DLQ
```

Two kinds of uploads share this pipeline: **documents** (SOPs, campaign plans, post-mortems) go through chunking and embedding into pgvector, while **data tables** (product sales, customer behavior) are parsed and inserted directly into SQL tables. A `contentKind` field in the message decides which path a file takes.

### Query flow

```
question + X-User-Id
  │
  ▼
backend /query ──forwards──▶ llm-service /query
  1. resolve the user → department, role
  2. check the cache (hit → return immediately)
  3. route: documents / data / both
     ├─ documents → vector search, filtered to the user's department plus company-wide docs
     └─ data      → Text-to-SQL: schema in the prompt → SELECT-only SQL
                    → department filter injected → execute
  4. retrieved passages + SQL rows + question + conversation history → LLM → answer
  5. write the cache and the session memory
```

---

## How it's built

### Tech stack

| Component | Role | Why |
|---|---|---|
| **Spring Boot 4** (Java 25) | Public API, file uploads, structured data | Mature service framework; JPA for the business tables |
| **Python / FastAPI** | Kafka consumer, RAG, Text-to-SQL, routing, memory | The AI ecosystem (LangChain, LlamaIndex, sentence-transformers) is richest in Python |
| **Kafka** (KRaft) | Async queue between upload and document processing | Parsing and embedding are slow; the upload API must not wait for them, and the processing side can retry and scale independently |
| **PostgreSQL + pgvector** | Business tables and vector search in one database | No separate vector database to operate; vectors and business rows can be queried together |
| **Redis** | Query cache and conversation memory | Repeated questions skip the LLM; session history needs fast reads and writes |
| **Docker Compose** | Local infrastructure | One command brings up Postgres, Redis, Kafka and Kafka UI |

### Key design decisions

**Reliable asynchronous ingestion**
- **At-least-once delivery.** The consumer disables auto-commit and commits an offset only after a message is fully processed or safely parked in the dead-letter topic. A crash before the commit means redelivery, never loss, so processing is idempotent and keyed by `uploadId`.
- **Permanent vs. transient failures.** Malformed messages and out-of-bounds paths are permanent and go straight to the dead-letter topic. Anything else is retried with exponential backoff before being dead-lettered.
- **A dead-letter topic that never loses data.** It stores the original bytes plus headers with the error, the attempt count and the source offset, so a message can be inspected and replayed. The consumer commits only after the dead-letter write is confirmed; if that write fails, it stops rather than drop the message. A bad message therefore neither blocks the ones behind it nor disappears.
- **"A timeout is not a failure" on the producer side.** When Kafka definitively rejects a send, the upload returns 503 and the stored file is deleted. When the outcome is unknown (a timeout), the file is kept, because the message may already be on its way and deleting the file would leave a message pointing at nothing.

**A cross-service contract that cannot drift silently**
- **Claim-check messages.** Events carry a logical `storageKey`, not a file path. Each service resolves the key against its own storage root, so the contract does not depend on the sender's working directory, operating system or machine, and the storage backend can change without changing the contract.
- **One source of truth.** [contracts/upload-event.examples.json](contracts/upload-event.examples.json) is validated by both the Java and the Python test suites. The Python side also parses the SQL schema to check that its enums match the database CHECK constraints.
- **Tolerant reader.** Unknown fields are ignored, so producers can add fields without breaking consumers; missing fields and unknown enum values are rejected.

**Untrusted input at every boundary**
- Client-supplied file names are sanitized at upload (path separators, reserved and control characters replaced), so a name like `../../evil.md` can never leave the upload directory.
- The consumer resolves each storage key to a real absolute path and checks that the **result** is still inside the upload directory, instead of pattern-matching the input for `..`, which misses absolute paths and symlinks.
- Upload metadata is validated against enums (document type, table type) and against the database (department and campaign must exist).

**Access control designed into the schema**
- Every document and data row carries a `department`; company-wide SOPs use an `ALL` marker.
- Vector search filters by department, which has a known pitfall: an approximate HNSW index combined with a `WHERE` filter can silently return fewer rows than requested. Retrieval accounts for this by using pgvector's iterative index scan or an exact scan.
- Text-to-SQL is restricted to `SELECT` statements, and a department filter is injected for non-admin users.

### Testing approach
- Web-layer slice tests with Kafka and the repositories mocked, so that broker failures, timeouts and interrupts can be triggered on demand and each failure path has a regression test.
- Contract tests on both sides of the Kafka boundary, plus schema-drift checks against the SQL definitions.
- The consumer loop takes its Kafka client, dead-letter producer, handler and clock as injected dependencies, so retries, dead-lettering and commit ordering are tested without a running broker or real waiting.
- Mutation testing on critical guarantees: each one was deliberately broken to confirm that the intended test fails.

### Repository layout

```
ops-knowledge-assistant/
├── backend-service/   # Spring Boot: REST API, uploads, Kafka producer
├── llm-service/       # Python: Kafka consumer, RAG, Text-to-SQL, routing, memory
├── contracts/         # cross-service message contract (validated by both test suites)
├── sql/               # database schema (PostgreSQL + pgvector)
├── sop_docs/          # sample SOP documents
├── campaign_docs/     # sample campaign plans and post-mortems
├── docs/              # architecture, data schema, permission model
└── docker-compose.yml # Postgres+pgvector, Redis, Kafka, Kafka UI
```

Sample documents and the design notes under `docs/` are written in Chinese, since the target users are Chinese-speaking operations teams. Local setup instructions are in [docs/development.md](docs/development.md).
