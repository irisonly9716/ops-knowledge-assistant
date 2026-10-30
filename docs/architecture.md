# Architecture and runtime flows

## Responsibilities of the two services

| Service | Language | Responsibilities |
|---|---|---|
| **backend-service** | Java / Spring Boot | public REST API, file upload endpoint, CRUD on structured business tables (JPA), publishing upload events to Kafka, forwarding `/query` to llm-service |
| **llm-service** | Python / FastAPI | Kafka consumer (parse → chunk → embed → store vectors), RAG retrieval, Text-to-SQL, query routing, conversation memory, caching |

Both services use **Kafka** and **Redis**:

- **Kafka**
  - `document-upload-events`: an employee uploads a file / SOP in the frontend → the backend stores the file and publishes an event → llm-service consumes and processes it
  - Future "automatic upload of the latest data": a scheduled producer packages the latest campaign / host data into a file every day and publishes it to the same topic, reusing the same pipeline -- hence the `source: manual | auto` field in the message
- **Redis**
  - query cache: identical / similar questions are answered directly, without retrieval + LLM again
  - conversation memory: `session:{id}` holds the multi-turn history
  - document processing status: `doc:{id}:status` = uploading / processing / done / failed (can also live in the database)

## Ingestion -- two pipelines, one topic

Uploads come in two kinds that are processed completely differently but share one Kafka topic (the
message's `content_kind` field tells them apart and the consumer branches on it; no second topic needed):

**`upload_id` vs `doc_id`**: the message carries the `upload_id` (a UUID) that backend-service generates,
not a `doc_id`. `documents.doc_id` is a Postgres `BIGSERIAL` that only exists once llm-service runs
`INSERT INTO documents ... RETURNING doc_id`; when backend-service publishes, that row doesn't exist yet,
so the id can't be known in advance. `upload_id` is also the Kafka consumer's **deduplication key** (Kafka
delivers at least once, so the consumer may receive duplicates).

**`storageKey`**: the message carries a LOGICAL KEY in storage (today: the file name), not a file path. A
path such as `uploads\\xxx.md`, relative to backend-service's folder and with Windows backslashes, means
nothing to a consumer in another folder, let alone on another machine. Each service reads `UPLOAD_DIR`
from `.env` to know where the storage root is and builds the real path itself; moving to S3 later keeps
the key and swaps only the storage backend. The consumer must guard against path traversal when it builds
the path (see `llm-service/app/consumers/storage.py`), because message content can't be trusted blindly.

**The single source of truth for the message contract** is
[contracts/upload-event.examples.json](../contracts/upload-event.examples.json). Java's
`UploadEventContractTest` and Python's `tests/test_events.py` both validate against that one file; if
either side quietly changes the format, its test fails.

**The consumer is a separate worker process**, not a background thread inside FastAPI:
`uv run python -m app.consumers.upload_consumer`. Reasons: a long-running blocking poll loop and HTTP's
async event loop are different kinds of work; independent lifecycles (restarting the web service doesn't
interrupt a document in progress); independent scaling (more workers in one consumer group share the
partitions); and as a plain blocking loop it only needs the synchronous confluent-kafka, no aiokafka.

**Delivery semantics: at least once.** Auto-commit is off; the offset is committed manually only after
processing (or once the message is safely in the dead-letter topic). Failures are handled at three levels:
- the message itself is broken (not valid JSON / breaks the contract / path outside the upload folder) →
  `PermanentError`, **no retry**, straight to the dead-letter topic
- any other exception during processing (file temporarily unreadable, downstream timeout) → retried with
  exponential backoff (3 attempts in total by default, i.e. the first try plus 2 retries, waiting 0.5 s
  and then 1 s), then the dead-letter topic. Note that the dead-letter topic is subject to Kafka's retention
  too (7 days in this project, `retention.ms=604800000`): expired dead letters are deleted automatically.
  The dead-letter topic is a holding area, not a permanent archive
- **the dead-letter topic `document-upload-events.dlq`** keeps the bad message as is, with the failure
  reason / attempt count / source offset in its headers. The offset is committed only after the dead-letter
  write is **confirmed**; if it can't be written, the process crashes -- better to stop than to lose a
  message. So a bad message neither blocks the messages behind it with endless retries (head-of-line
  blocking) nor disappears silently.

**① Documents** (the narrative content of SOPs / campaign plans / reviews) -- free text, goes through RAG:

```
[manual upload by an employee]  POST /documents/upload (backend)
      │  stores the file in uploads/ or S3, generates upload_id
      ▼
  Kafka: document-upload-events   { upload_id, storage_key, content_kind: "document",
                                     doc_type, department, campaign_id?, category?, source: "manual" }
      │
[future] scheduled job ──same topic──▶   { ..., source: "auto" }
      │
      ▼
  llm-service Kafka consumer
      │  1. download / read the file
      │  2. parse (Markdown / docx / digital PDF → headings, paragraphs, list items, tables)
      │  3. chunking (RecursiveCharacterTextSplitter)
      │  4. embedding (one vector per chunk)
      │  5. write to Postgres: INSERT documents (only now does the real doc_id exist) + doc_chunks
      │  6. set the status to done
      ▼
  searchable by vector retrieval
```

**② Structured tables** (product sales and customer behavior tables uploaded by ops, e.g. Excel/CSV
exports) -- not chunked, not embedded; parsed straight into structured rows and queried with Text-to-SQL:

```
[employee uploads Excel/CSV]  POST /documents/upload (backend, same endpoint)
      │  generates upload_id
      ▼
  Kafka: document-upload-events   { upload_id, storage_key, content_kind: "data_table",
                                     table_type: "campaign_products" | "campaign_customer_behavior",
                                     campaign_id, source: "manual" }
      ▼
  llm-service Kafka consumer
      │  1. download / read the file
      │  2. parse the table (pandas / openpyxl)
      │  3. INSERT straight into campaign_products / campaign_customer_behavior by table_type
      │  4. set the status to done
      ▼
  queryable by Text-to-SQL
```

Neither pipeline is an analytics engine of ours -- **they only store data ops already has or content ops
wrote**. The real analysis (why a campaign did well or badly) is the LLM's job, attributing results by
combining the plans / reviews from ① with the structured data from ② and `campaign_metrics`. That is the
core value of this project.

## Query flow

```
user question + X-User-Id
      │
      ▼
backend /query ──forwards──▶ llm-service /query
      │
      │ 1. resolve the current user → department, role
      │ 2. check the cache (a hit returns immediately)
      │ 3. route: documents / data / both?
      │
      ├── documents → vector search (filter: department IN (user's department, 'ALL'))
      │
      ├── data → Text-to-SQL
      │             - schema in the prompt
      │             - generate SQL (SELECT only)
      │             - inject WHERE department = user's department (when role=staff)
      │             - execute → result
      │
      │ 4. retrieved chunks + SQL result + question + conversation history → LLM → answer
      │ 5. write the cache + the conversation memory
      ▼
   back to the user
```

## Ports (local development)

| Service | Port |
|---|---|
| Postgres | 5432 |
| Redis | 6379 |
| Kafka | 9092 |
| Kafka UI | 8080 |
| backend-service | 8081 |
| llm-service | 8000 |

## Key design decisions

- **Why llm-service consumes from Kafka instead of the backend calling it over HTTP**: parsing + embedding
  can take seconds to tens of seconds and the upload endpoint must not wait for it; failures must be
  retryable; a future automatic producer can reuse the same path.
- **Why document parsing uses one small library per format instead of Unstructured**
  (markdown-it-py / python-docx / pdfplumber, behind one `DocumentParser` interface): measured on the same
  samples. `unstructured` installs 372 MB without any format support (spaCy, numba) and 1.5 GB with PDF
  support (torch, OpenCV), none of which its fast PDF mode uses; at parse time it also downloads an English
  spaCy model from GitHub to decide whether a line is a heading or body text. In quality, it found no heading
  levels in PDFs, read headings as body text and body text as headings, and shredded tables into dozens of
  fragments; nested Markdown lists were flattened too. The three small libraries total 54 MB, the PDF
  structure rules are our own, and all three formats parse into the same structure, text included (covered
  by tests). Scanned PDFs would get an OCR implementation behind the same interface (say, Amazon Textract).
- **Why vectors and structured data both live in Postgres**: one less piece of infrastructure to run, and
  `doc_chunks` and the business tables can be queried in the same transaction / connection.
- **Why `department` is built into the schema from the start**: added at the end, the vector table, SQL
  generation and retrieval filters would all need rework. See [permission-model.md](permission-model.md).
- **Why document and structured-table uploads share one topic instead of two**: their message shapes
  (`upload_id` / `storage_key` / `department`) mostly overlap, and branching on one `content_kind` field plus
  an if/else in the consumer is enough; two topics would add one more topic to create and configure and
  nothing else. The principle: split topics by *semantically different kinds of events*, not to make code
  branches look tidier.
- **Why ROI / per-product attribution / customer segments are not structured fields**: this project's core
  value is "AI analyzes content and results", not rebuilding a BI computation engine. Clean numbers
  (GMV / DAU / conversion rate / concurrent viewers) go into `campaign_metrics`; content that needs judgment
  and attribution goes into `documents` (the review); raw data ops already has but we shouldn't recompute
  (product sales, customer behavior) goes into its structured table as is, with no computation layer of ours
  in between.
