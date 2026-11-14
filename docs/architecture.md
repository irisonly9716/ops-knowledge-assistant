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
  - query cache: the same question, after it has been rewritten into a standalone question with concrete
    references, from the same department and role against the same knowledge-base version is answered directly, without retrieval + LLM
    again. No similarity-based cache: "last month's GMV" and "the month before's GMV" are similar questions
    with different answers
  - conversation memory: `session:{id}` holds the multi-turn history
  (Processing status lives in the `uploads` table in Postgres, not in Redis: it must survive restarts and
  is written in the same transaction as the document.)

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
  message. Time counts too: one message gets one budget (200 s) across all its attempts and every retry layer
  below them, safely under `max.poll.interval.ms`; retries alone used to add up to 590 s or even 30 minutes
  against a hanging or rate-limiting API. So a bad message neither blocks the messages behind it with endless retries (head-of-line
  blocking) nor disappears silently.

**① Documents** (the narrative content of SOPs / campaign plans / reviews) -- free text, goes through RAG:

```
[manual upload by an employee]  POST /documents/upload (backend)
      │  stores the file in uploads/ or S3, generates upload_id
      │  one transaction: uploads row (received) + outbox row holding the event → 202
      ▼
  outbox relay (backend, every second): publish unpublished rows, mark them published
      ▼
  Kafka: document-upload-events   { upload_id, storage_key, content_kind: "document",
                                     doc_type, department, campaign_id?, category?, source: "manual" }
      │
[future] scheduled job ──same topic──▶   { ..., source: "auto" }
      │
      ▼
  llm-service Kafka consumer (app/consumers/handlers.py)
      │  1. short transaction: lock the uploads row; already done → duplicate, skip; else → processing
      │  2. no transaction:
      │     read the file → parse (Markdown / docx / digital PDF → headings, paragraphs, list items, tables)
      │     → fields (title / department from the upload, never from the file) → catalog labels
      │     → chunking (by section, up to 600 tokens, contextual header) → embedding (batched)
      │  3. short transaction: lock the row again, INSERT or replace documents (only now does the
      │     doc_id exist), delete + insert doc_chunks, campaign flags, status done
      ▼
  searchable by vector retrieval
```

**The uploads row is the source of truth, the message is only a notification.** The backend writes the row
(`received`, with the uploader from `X-User-Id`) in the same transaction as the outbox row carrying the event,
and the event is published only after that commit, so the consumer always finds the row. Its status moves
`received → processing → done`; a message that ends in the dead-letter topic marks it `failed` with the reason.

**Transaction boundaries.** Embedding is a network call that can take seconds and be retried, so it runs with
no transaction open; everything that becomes visible (document, chunks, flags, status) is written afterwards in
one short transaction. A failure before or inside that transaction leaves no half-written document. To build
the chunk headers before the document row exists, the labels of `document_catalog` come from the
`catalog_entry()` SQL function, which the view uses as well.

**Idempotency.** Kafka delivers at least once, so a message can arrive twice. The uploads status is the
deduplication record: an upload that is already `done` is skipped, and the check is repeated under the row
lock right before writing, where it can't race with another copy of the message. A replacement updates the
row produced by `replaces_upload_id` in place (same `doc_id`) and rebuilds its chunks; if that version was
replaced in the meantime, the upload fails instead of overwriting the newer one (optimistic concurrency).

**Transactional outbox: no dual write.** Recording an upload and publishing its event touch two systems with
no shared transaction; done one after the other, a crash in between leaves an upload that is recorded but
never published. So the upload request does not publish at all. It writes the uploads row and an `outbox` row
holding the serialized event in one database transaction, and returns `202` once that commits. A relay in the
backend (`OutboxRelay`, every second) locks the oldest unpublished rows with `FOR UPDATE SKIP LOCKED`, publishes
them in order, waits for Kafka's acknowledgement and marks them published. A failed or unacknowledged send is
recorded on the row (`attempts`, `last_error`) and retried later, backing off up to 30 seconds while Kafka is
down; the batch stops at the first failure so messages keep their order.
- A Kafka outage no longer fails uploads; it only delays processing. Measured: with Kafka stopped an upload is
  still accepted, the relay retries with backoff, survives a backend restart, and publishes as soon as Kafka is
  back.
- Delivery from the outbox is at least once: a relay that crashes after Kafka acknowledged but before marking
  the row publishes the message again. The consumer is idempotent on the uploads status, so the second copy is
  skipped. This is why the outbox needs an idempotent consumer.
- `SKIP LOCKED` lets several backend instances relay side by side: a row one of them holds is skipped by the
  others instead of being waited for or published twice at the same moment.
- Published rows are kept for 7 days for troubleshooting, then deleted.

Not chosen: change data capture (Debezium reads the database log; it needs a Kafka Connect deployment, more
infrastructure than this project warrants), and publishing first and recording afterwards (the same gap, mirrored).

**Reconciliation: checking what really happened.** The outbox guarantees that an event gets published. It can't
guarantee what comes after: a broker can lose its disk, or a message can reach the dead-letter topic while recording
`failed` doesn't succeed. Either way the upload stays unfinished, with nothing left to move it. So a job in the backend
(`UploadReconciler`, every minute) looks at unfinished uploads with no activity for 5 minutes and decides by the lag of
the consumer group that owns their kind, not by their age:

| What it finds | What it means | What it does |
|---|---|---|
| The event is still unpublished in the outbox | Waiting to be sent, not stuck | Nothing |
| Lag above 0, the position moving | A backlog: a message is still in Kafka | Nothing |
| Lag above 0, the position still for 10 minutes | The consumer is dead or stuck | An error in the log, publishes nothing |
| Lag 0 | Nothing is waiting or in flight, so the message is gone | Publishes the event again through the outbox, at most 3 times, then marks it `failed` |
| Kafka doesn't answer | Nobody can say that a message is gone | Nothing |

Age alone can't decide this, because `received` includes queueing: during an embedding API outage on one partition,
everything behind the first message looks stuck, and publishing all of it again would double the backlog at the worst
moment. Lag 0 is a reliable signal because a message being processed isn't committed yet. Publishing twice is harmless,
because the consumer is idempotent, and that is what makes this safe when two backend instances run it at once.
The alert is an error in the log; turning it into a page depends on where this is deployed.

**A file that kills the consumer.** The consumer's retry count lives in memory, so a file that kills the process (out of
memory, a crash in a parsing library) comes back with a fresh count after every restart, never reaches the dead-letter
topic, and on one partition blocks everything behind it. So `uploads.delivery_count` goes up in the database once per
delivery, before any work. After 3 deliveries that didn't finish, the next one goes to the dead-letter topic and the
upload is marked `failed`. It counts deliveries and not retries, because one delivery normally retries 3 times. It
starts over when an upload ends in `done` or `failed`, so replaying from the dead-letter topic isn't refused. It isn't
limited to 1, because a restart during normal work costs a delivery too.

**② Structured tables** (product sales and customer behavior tables uploaded by ops as CSV exports) -- not
chunked, not embedded; validated and loaded straight into structured rows, queried with Text-to-SQL:

```
[employee uploads a CSV]  POST /documents/upload (backend, same endpoint)
      │  stores the file, generates upload_id
      │  one transaction: uploads row + outbox row holding the event → 202
      ▼
  outbox relay → Kafka: document-upload-events   { upload_id, storage_key, content_kind: "data_table",
                                     table_type: "campaign_products" | "campaign_customer_behavior",
                                     campaign_id, source: "manual" }
      ▼
  llm-service Kafka consumer (app/consumers/data_tables.py)
      │  1. short transaction: lock the uploads row; already done → duplicate, skip; else → processing
      │  2. no transaction: read the file, validate every cell against its column
      │     (any problem → dead-letter + failed, with "row 3, units_sold: 'abc' is not a whole number")
      │  3. one transaction: lock the campaign, check the version this upload replaces,
      │     DELETE the campaign's rows + COPY the new ones + set done
      ▼
  queryable by Text-to-SQL
```

Why it differs from the documents:
- **No chunks, no embeddings.** The questions are "how many", "which product", "what's the average": SQL answers
  them exactly, while similarity search over rows of numbers would answer them approximately at best.
- **A whole table is one version.** An upload replaces all of a campaign's rows in one transaction, so a reader sees
  the old set or the new one, never a mix. There is no row-level merge: the export is the source of truth.
- **Per-cell checks with `csv`, for now.** The point is a precise error for a person to fix. The limits in the checks
  mirror the column types, so nothing that passes can be refused by the database with an error that looks temporary.
  Measured at the 20 MB upload limit, `csv` and pandas take about the same time (1.7 s), so there was no reason yet to
  add a dependency. The known costs: the whole file is held in memory (about 440 MB at the limit), and ops staff work
  in Excel, so converting to CSV costs them time on every upload. Excel uploads, parsed with pandas, are
  [issue #9](https://github.com/irisonly9716/ops-knowledge-assistant/issues/9).
- **`COPY`, not `INSERT`s.** The whole load is one statement, so `statement_timeout` limits all of it. Measured: a
  batch of `INSERT`s ran 4.3 s under a 0.2 s limit, because the limit applies to each one. It is also about three
  times faster: at the 20 MB upload limit (about 300,000 rows), about 2 s to validate and 3 s to load.
- **Worst case.** The write is bounded by two lock waits (10 s each) plus `DELETE` and `COPY` (30 s each): 80 s, and
  it only starts if that fits what is left of the message's 200 s budget. So the write always ends inside the
  budget. The latest an attempt can stop is one started just before the budget ends: connect and lock (20 s) and
  validation (about 2 s) before that check refuses to write, about 222 s, plus the dead-letter write, under the 300 s
  `max.poll.interval.ms`. If the limit were passed anyway, the transaction rolls back or the duplicate is skipped.

Neither pipeline is an analytics engine of ours -- **they only store data ops already has or content ops
wrote**. The real analysis (why a campaign did well or badly) is the LLM's job, attributing results by
combining the plans / reviews from ① with the structured data from ② and `campaign_metrics`. That is the
core value of this project.

## Query flow

```
user question + identity (X-User-Id today; a verified token later, see issue #5)
      │
      ▼
backend /query
      │ 1. resolve the current user → department, role (from the users table, never from the request)
      │
      ├──forwards with the internal service token──▶ llm-service /query (accepts calls from the backend only)
      │
      │ 2. rewrite the question into a standalone one, using the turns before it ("and the time before?"
      │    only means something together with them), and make relative references concrete ("the last
      │    Black Friday" → "Beauty Black Friday Live, which ran from 2025-11-18 to 2025-12-14", "last month"
      │    → "October 2026 (2026-10-01 to 2026-10-31)"). The model only marks a relative reference; a query
      │    through the read-only role and row-level security finds which campaign it means, so only
      │    campaigns this user can see are candidates, and code works out the dates. If that doesn't settle
      │    it, the question is asked back with the candidates. Runs beside the router, not before it
      │ 3. check the cache: key = department + role + knowledge-base version + rewritten question
      │    (a hit returns immediately)
      │ 4. route: documents / data / both / none? One model call with structured output; below 0.95 confidence
      │    both paths run, and a keyword rule decides if the model doesn't reply in 2 s. The embedding and both
      │    paths start at the same time as the router, and the route cancels the path it rules out
      │
      ├── documents → vector search (filter: department IN (user's department, 'ALL')), top 50
      │               → reranker (a cross-encoder in its own service) → top 5
      │
      ├── data → Text-to-SQL, a fixed pipeline
      │             - data dictionary + metric views + the names in the data + the 3 closest example questions
      │               in the prompt
      │             - generate SQL; sqlglot checks the syntax tree: one SELECT, allow-listed views and functions;
      │               every view it reads becomes a subquery holding only the user's department's rows
      │             - execute as a read-only role that can only read the views, in a read-only transaction
      │               with a statement timeout and at most 201 rows, under row-level security on the tables
      │             - on an error, show the model the error and generate again (2 retries at most)
      │
      │ 5. documents → their answer, with citations; data → the rows explained in a few sentences;
      │    both → one answer written from the chunks and the rows together. A campaign found in step 2
      │    opens the answer ("Based on ..., which ran from ... to ..."), so a wrong reading is easy to spot
      │ 6. write the cache + the conversation memory + the audit record
      ▼
   back to the user
```

The cache and the permission boundary meet in the key: without the department and role, one department's answer
could be served to another. A new or replaced document bumps the knowledge-base version of its department (an `ALL`
document: every department's), and so does a data-table upload, which changes SQL answers. The version lives in
Postgres and goes up in the same transaction as the write: in Redis it would be another dual write, and a crash
between the two would keep serving old answers. Relative references are made concrete before the key is built, so
when a new campaign finishes the key changes; a question that still has one after rewriting skips the cache. The
TTL is only a safety net.

**What exists today:** the backend's `/query`, the internal token, and steps 1, 2, 4 and 5 (`app/query_flow.py`): the
rewrite with the campaign search and the dates, asking back, the router, the search with its department filter and the
reranker, the Text-to-SQL pipeline with its checks, department filter, read-only role and row-level security, and one
answer from either or both, with citations, refusals and the campaign it is about. The turns before a question come
from the evaluation only: `/query` has none until there is conversation memory. A baseline agent for Text-to-SQL is
measured on the evaluation dataset. The cache (3) and step 6 are still to come. Details
and measurements: "The query path" in [llm-service/README.md](../llm-service/README.md). How well it answers, measured on the evaluation
dataset, and why the refusal threshold and the answer instructions are what they are:
[eval-report.md](eval-report.md).

Design decisions so far:
- **The rewrite runs beside the router, not before everything.** A model call in front of every question would add its
  time to all of them, about 1.2 s; most questions stand on their own and lose nothing this way. One that is rewritten
  stops the paths that had started on it and starts them again on the question filled in, so it pays that time.
  Not taken: the router rewriting too (one call fewer, but its instructions and 0.95 threshold were measured as they
  are), and a keyword check deciding when to rewrite ("the previous batch" and "next time" would pay for nothing).
- **A model marks a reference; code resolves it.** The rewrite writes `{campaign}` or `{period}` with what kind of
  reference it is (a closed set: the most recent finished campaign, of a kind, or the n-th before it; this or last month
  or year, or the last n full months). Which campaign is a query: the asker's department's finished campaigns, latest
  end date first, run as `sql_reader` under row-level security with the department in the WHERE clause too, so another
  department's campaign reads exactly as one that doesn't exist. Dates are code. Left to a model, the SQL writer once
  read "last Black Friday" as the day of the holiday.
- **Asked back rather than guessed.** An executive's "the last Black Friday" gets each department's, since every
  department runs one; two campaigns ending on the same day get both. Candidates come from the same search, so only
  campaigns the asker may see. Nothing found is one fixed sentence: there is nothing to choose from.
- **The basis line is code, not the model's**, which could drop or alter it, and it is the point of it. When the
  documents were searched and the campaign has no review yet, the line says so and names the latest before it that
  has one. Whether one exists is `campaigns.has_review_doc`, read as `ops` by id, for campaigns the search already
  found: no view has it, and adding it to one would show the SQL writer a column that is a routing shortcut.
- **Both paths start before the route is known.** Routing is a model call, p50 1.2 s and p95 1.5 to 1.7 s on a
  development machine, and in front of the paths it would put a question about data at about 3.8 s, estimated from each
  step's measured time, against the 3 s target. So the embedding, the documents' answer and the data path's first query
  run while the router decides, and the route cancels the path it rules out. The price is the model calls thrown away:
  a query being written for a question about documents, and a documents' answer for a question answered from both.
- **The top is asyncio; each path is a LangGraph state graph.** LangGraph runs a graph in steps, and the branches of a
  step all wait for the slowest one: measured with sleeps standing in for the calls, a 0.5 s chain of three nodes beside
  a 1 s router finished at 1.47 s, and as one subgraph node at 0.53 s. And a running branch can't be stopped from inside
  the graph. So asyncio starts the paths, cancels one and joins the rest, while the paths, whose steps follow each other
  anyway, are graphs: the data path's retry is a loop with its bound on the graph, drawn in the README. A graph run costs
  0.35 ms plus 0.33 ms a step of CPU, about 3 ms a question for the two.
- **Cancelling a path stops its work.** A cancelled model call ends its request, and psycopg cancels a statement running
  in Postgres when the task waiting on it is cancelled: measured, 5 ms, with nothing left running on the server and the
  connection working again. Whatever happens, every task a question started is cancelled and waited for before it
  returns, so nothing holds a model call slot or a connection for a question nobody waits for.
- **One answer from both, in one call.** With excerpts and rows, one model call writes the answer from the two together.
  Answering from each and merging would be a model call more, about a second, and the merge could drop citations.
- **The reranker is a service of its own.** bge-reranker-v2-m3 needs a GPU: on a 6-core CPU it took about 40 s for 50
  candidates. In llm-service each of the 4 workers would load it (1.9 GB each) and its computing would stop the event
  loop. In Text Embeddings Inference it is one model on one GPU, scaled apart from llm-service, which sends it one HTTP
  request per query. gRPC would save a few milliseconds on a small request and add generated code and two
  dependencies. Its latency on a GPU, and how many GPUs 15,000 pairs a second at the peak take, aren't measured (#11).
- **Async in llm-service, blocking in the backend.** Sync FastAPI endpoints get 40 worker threads against 900 queries in
  flight. Spring keeps ordinary blocking code, because virtual threads remove its limit with one setting.
- **A connection pool** (`psycopg-pool`, 171 KB). Measured with 300 queries at once: 6,774 per second with no failures,
  against 44 per second with 108 of 300 failing without one, because Postgres allows 100 connections.
- **No database connection while waiting for a model**, on either side. In the backend that took switching off
  `spring.jpa.open-in-view`, whose default limited the whole service to 10 queries in flight.
- **Time is limited twice in llm-service**: a hard stop that cancels, and a budget the layers consult so a retry that
  can't fit gives up with the real reason.
- **Two limits, in two places, for two different things.** The rate limit in the backend is about how many queries come in
  per second: `429` at once. The limit on model calls in llm-service is about how many run at the same moment, which also
  grows when the model slows down: `503` after at most 1 s. By Little's law, 300 QPS at 3 s is 900 at once.
- **The rate limit is in memory, not in Redis**: per instance and one bucket for everyone. Redis would share it across
  instances at the price of a network call and one more failure on every query.
- **Virtual threads in the backend.** Measured at 300 QPS with a stub answering in 3 s: p50 8.23 s on Tomcat's 200 threads,
  3.21 s on virtual threads. They also remove what used to limit how many requests wait for a database connection, so that
  wait now has its own limit of 2 s.
- **Several llm-service processes, on one port.** One process reaches about 100 QPS, measured on Linux: past that, the
  limit is one CPU core. Production runs `python -m app.server --workers 4`, which met the 300 QPS peak; 500 QPS took 8.
  Uvicorn's workers need no load balancer, at the price of an uneven share of the work, so each process gets 300 model
  call slots rather than an exact 225, and 5 database connections because all their pools share the 100 Postgres allows.
  Numbers: "The query path" in [llm-service/README.md](../llm-service/README.md). Measured on the documents path before
  routing: a routed query holds up to four model calls at once while the router decides, and has a second pool, of 5
  connections, for generated SQL; what that takes at the peak isn't measured (#11).
- **Text-to-SQL is a fixed pipeline, not an agent.** The baseline agent spent 4 model calls a question finding a schema
  the code already knows, p50 about 9 s on dev. The pipeline puts the five views with their comments (about 1,050
  tokens), the names in the data and the 3 closest example questions into one prompt: 2 model calls, writing and
  explaining, p50 2.4 s. There is no step that picks tables: it would cost a model call to save fewer tokens than it
  spends.
- **The department filter on generated SQL is code, not an instruction.** sqlglot replaces every view a query reads with
  a subquery holding only the asker's department's rows. The department isn't in the SQL text: the subquery reads the
  setting `app.department`, which the code sets with `SET LOCAL` in the same transaction, and a query can't set it
  itself because the function allow-list has neither `set_config` nor `current_setting`. What runs is regenerated
  from the checked tree, never the model's text, so what was checked is what runs: 167 queries regenerated this way
  returned the same rows as their originals.
- **The database keeps the department too.** The model's query runs as `sql_reader`, which can read the five views and
  nothing else, and row-level security on the tables behind them reads the same setting, so either layer holds if the
  other has a bug. The views belong to a role of their own that is subject to row-level security: owned by `ops` they
  would bypass it, and read as the asker (`security_invoker`) they need the asker to read the tables. `set_config` is
  revoked, because a `SELECT` can call it and change the department halfway through the query; `SET LOCAL` is a
  statement and can't be part of one. Reading every campaign's summary took 2.6 ms under row-level security against
  1.6 ms without it (1.0 ms for one department), on a copy of the development data: next to model calls of a second
  or more, it doesn't show.
- **Generated SQL returns at most 201 rows**, by a `LIMIT` on the outer query, so Postgres stops there: psycopg
  receives a whole result before handing over any of it, and 2 million rows took 2.0 s and 156 MB more memory to keep
  200 of them, against 0.2 s with the limit.

## Ports (local development)

| Service | Port |
|---|---|
| Postgres | 5432 |
| Redis | 6379 |
| Kafka | 9092 |
| Kafka UI | 8080 |
| Reranker (Text Embeddings Inference, only with `--profile rerank`) | 8082, on localhost: it is sent the documents' text |
| backend-service | 8081 |
| llm-service | 8000: for the backend only. It trusts the role in the request, so it must never be reachable from outside |

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
- **Why chunks follow sections instead of a fixed size**: an SOP question is usually about one procedure,
  and a fixed-size cut can split a step in half or glue the end of one section to the start of the next.
  Chunks never cross a heading; inside a section, elements are packed up to 600 tokens and a step with its
  nested items is never split. Measured on the samples: a whole procedure is 233-303 tokens and a single
  step only about 50, so one chunk per step would be too small to embed well, and "how do I handle X"
  would get back only the top-k steps of seven. Every chunk starts with a contextual header (document type,
  department, campaign, date, host, heading path) that is embedded with the text. LangChain's
  `RecursiveCharacterTextSplitter` is only used for a unit larger than the limit by itself, overlapping by
  whole sentences. Section-level vs step-level chunks is a question for the eval; the limit is one parameter.
- **Why vectors and structured data both live in Postgres**: one less piece of infrastructure to run, and
  `doc_chunks` and the business tables can be queried in the same transaction / connection.
- **Why `department` is built into the schema from the start**: added at the end, the vector table, SQL
  generation and retrieval filters would all need rework. See [permission-model.md](permission-model.md).
- **Why document and structured-table uploads share one topic instead of two**: their message shapes
  (`upload_id` / `storage_key` / `department`) mostly overlap, and branching on one `content_kind` field plus
  an if/else in the consumer is enough; two topics would add one more topic to create and configure and
  nothing else. The principle: split topics by *semantically different kinds of events*, not to make code
  branches look tidier.
- **Why per-product attribution and customer segments are not structured fields**: this project's core
  value is "AI analyzes content and results", not rebuilding a BI computation engine. Clean numbers
  (GMV / DAU / conversion rate / concurrent viewers) go into `campaign_metrics`; content that needs judgment
  and attribution goes into `documents` (the review); raw data ops already has but we shouldn't recompute
  (product sales, customer behavior) goes into its structured table as is, with no computation layer of ours
  in between. ROI is the one figure computed here, because it is a fixed formula over two uploaded numbers: the
  `campaign_summary` view (V9) writes it once, so no generated query has to get it right again. Why a campaign's
  ROI came out as it did stays in its review.
