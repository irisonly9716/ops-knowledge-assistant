# llm-service

Python / FastAPI. Embeddings, RAG, Text-to-SQL, query routing, conversation memory.

## Running it locally

Python is pinned to **3.12** (see `.python-version`). [uv](https://docs.astral.sh/uv/) is recommended:

```bash
cd llm-service
uv python install 3.12
uv venv
uv pip install -r requirements.txt
uv run uvicorn app.main:app --reload --port 8000
```

http://localhost:8000/health returning `{"status":"ok",...}` means it works.
http://localhost:8000/docs is the generated API documentation.

<details>
<summary>Without uv, using a standard venv</summary>

```bash
cd llm-service
py -3.12 -m venv .venv          # needs Python 3.12 installed first
.venv\Scripts\Activate.ps1      # WSL/macOS: source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```
</details>

## Layout

```
app/
├── main.py        FastAPI entry point (only /health for now)
├── config.py      environment variables (the repository-root .env)
├── db.py          Postgres connections (autocommit; transactions are explicit `with conn.transaction():` blocks)
├── consumers/     Kafka consumer (the document pipeline), a separate worker process
│   ├── upload_consumer.py   consume loop: manual commits + retries + dead-letter topic + graceful shutdown; entry point main()
│   ├── events.py            the Python half of the message contract (pydantic, mirrors Java's UploadEvent)
│   ├── storage.py           storageKey -> the real file in the upload folder (with path traversal defense)
│   ├── handlers.py          the ingestion pipeline: uploads status, parse, chunk, embed, store; idempotent
│   ├── data_tables.py       CSV uploads: validate every cell, then replace a campaign's rows in one transaction
│   └── errors.py            PermanentError: errors where retrying is pointless, straight to the dead-letter topic
├── rag/           parsing / chunking / embedding / retrieval / Q&A
│   ├── parsing/             Markdown / docx / PDF -> the same typed elements (headings, paragraphs, list items, tables)
│   ├── chunking.py          elements -> chunks: by section, token limit, contextual header, SOP steps
│   ├── document_fields.py   which values a documents row gets (the upload decides, the frontmatter fills gaps)
│   ├── embeddings.py        Embedder interface + OpenAI implementation + batching
│   └── store.py             SQL for ingestion: uploads row, catalog labels, documents + doc_chunks
├── sql_agent/     Text-to-SQL + safety checks + row-level filtering
├── router/        query routing + combined answers
└── memory/        Redis cache + conversation memory
tests/             pytest; test_events (contract / cross-language drift) test_storage (path traversal)
                   test_upload_consumer (every branch of the consume loop) test_health
                   test_embeddings (fake OpenAI client) test_parsing (all three formats parse the same)
                   test_chunking (steps stay whole, sections stay apart, oversized units split with overlap)
                   test_data_tables (every refusal says what to fix and where)
                   test_ingestion (real Postgres: idempotency, rollback, replacements, conflicts, for documents and tables)
tests/fixtures/documents/   docx / PDF fixtures, generated from Markdown by scripts/make_sample_documents.py
```

## Document parsing

`app.rag.parsing.parse_file(path)` picks a parser by extension (`.md` / `.docx` / `.pdf`) and returns a
sequence of elements: kind (heading / paragraph / list item / table), level, heading path, and for PDFs the
page number. Chunking works only with these elements and doesn't care about the original format.

- **Markdown** (markdown-it-py), **docx** (python-docx): the formats carry their structure, it is read directly
- **PDF** (pdfplumber): only "what was drawn where, at which size"; structure is inferred from font size,
  indentation and line spacing, and spaces from the gaps between letters. Only digital PDFs with a text
  layer are supported; scans are rejected (they need OCR)
- broken file, unsupported format, wrong encoding, PDF without a text layer → `DocumentParseError`; retrying won't help

After changing a sample Markdown file, regenerate the docx / PDF (needs Windows + Word or WPS):

```bash
uv run python -m scripts.make_sample_documents
```

## Chunking

`app.rag.chunking.chunk_document(parsed, info)` turns the elements into chunks:

- **one section at a time**: a chunk never crosses a heading. A section's elements are packed in order up
  to 600 tokens; a step (top-level numbered list item) and its nested items are never split. Every sample
  SOP section fits into one chunk (a whole procedure is 233-303 tokens with its header), so "how do I handle X" retrieves
  the whole procedure
- **only a unit larger than the limit on its own is cut further**: a table by rows, repeating the header
  row; text by LangChain's `RecursiveCharacterTextSplitter`, overlapping by whole sentences (up to 80 tokens)
- **a contextual header** on every chunk, embedded and stored with the text:
  ```
  SOP | All departments
  Account Restriction Handling > Procedure

  1. Identify the rule and the account: ...
  ```
  The labels come from `catalog_entry()` (`db/migration/V5`), the function behind the `document_catalog` view
- list numbers are written back (`3. Collect appeal evidence`), since docx and PDF don't store them as text
- `sop_steps(parsed)` gives the `documents.steps` value; `document_fields(...)` decides title / department /
  category / responsible_team (title and department always come from the upload, never from the file)

Lengths are tokens of the embedding model's tokenizer (`tiktoken`, `cl100k_base`). **tiktoken downloads its
1.7 MB encoding file on first use** and caches it, so the first test run needs the network; a deployment
image should download it at build time (set `TIKTOKEN_CACHE_DIR` and call `tiktoken.get_encoding` once).

## Running the consumer

```bash
uv run python -m app.consumers.upload_consumer
```

**It is a process separate from FastAPI** (`uvicorn app.main:app` is the other one). Prerequisites: Kafka
running and the topics created (`init_topics`, which includes the dead-letter topic
`document-upload-events.dlq`), Postgres migrated, and `OPENAI_API_KEY` in `.env` (checked at startup). The
startup log prints the resolved `upload_dir`: mismatched upload folders (the two services pointing at
different directories) is the most common deployment problem.

What it does with each upload (`app/consumers/handlers.py`, reasons in [docs/architecture.md](../docs/architecture.md)):
1. **short transaction**: lock the `uploads` row (written by the backend before publishing); already `done` →
   duplicate, skipped; otherwise → `processing`
2. **no transaction**: read the file, parse, decide the fields, chunk, embed in batches
3. **short transaction**: lock the row again (a duplicate may have finished meanwhile), insert or replace the
   document, rebuild its chunks, set the campaign flag, mark the upload `done`

The log shows every step:
```
upload_id=d16352d3-... processing: document sop, department=ALL, file=d16352d3-...-account-restriction-handling.md
upload_id=d16352d3-... parsed 17 elements into 3 chunks (428 tokens)
upload_id=d16352d3-... embedded 3 chunks with text-embedding-3-small: 428 tokens billed, 1.62 s
upload_id=d16352d3-... done: doc_id=3 (new, 'Account Restriction Handling') with 3 chunks in 1.87 s
```
Measured: 0.7-1.9 s per sample document, almost all of it the embedding call (the first message also loads
the tokenizer).

**Time limit.** Kafka's `max.poll.interval.ms` (5 minutes) is the longest a consumer may go between two polls
before it is removed from the group. One message is handled between two polls, so it has to stay below that
even when everything goes wrong, and the normal case says nothing about the worst one: with a hanging or
rate-limited embedding API, retries in several layers used to add up to about 590 s (every request timing out)
or 30 minutes (429 with `Retry-After: 120`). So one message gets **one time budget** (200 s,
`doc_consumer_message_budget_seconds`), shared by all its attempts:
- the embedder does its own retries (the OpenAI SDK's are off) and caps every request timeout and every wait,
  including `Retry-After`, by the time left; if a wait doesn't fit, it gives up at once
- the consumer starts another attempt only if the backoff fits; when the budget is gone, the message is dead-lettered
- database waits are bounded too (`connect_timeout`, `statement_timeout`, `lock_timeout`). A data table loads with
  one `COPY`, so `statement_timeout` limits the whole load (worst case in [docs/architecture.md](../docs/architecture.md))
- at startup the consumer refuses settings where the budget plus 60 s (dead-letter write, commit) doesn't fit
  `max.poll.interval.ms`
- if the limit is passed anyway, the late commit fails with `UNKNOWN_MEMBER_ID`. That is logged and the loop goes
  on: the message is delivered again and idempotency handles the second copy

Behavior (design reasons in [docs/architecture.md](../docs/architecture.md)):
- **at least once**: auto-commit off; the offset is committed only after processing (or once the message is safely dead-lettered)
- broken message (not JSON / breaks the contract / path outside the upload folder), unparseable file, a CSV that fails
  validation, no uploads row, a conflicting or stale document or table → no retry, straight to the dead-letter topic
  `document-upload-events.dlq`
- any other exception → exponential backoff (at most 3 attempts = first try + 2 retries, waiting 0.5 s, 1 s, while the message's time budget lasts), then the dead-letter topic; dead letters carry the failure reason, attempt count and source offset
- dead-letter write fails → no commit, and the process crashes (better to stop than to lose a message)
- once a message is dead-lettered, its upload is marked `failed` with the reason
- after being killed hard, a restarted consumer waits for the old member's **session timeout** (about 45 seconds) before taking over the partitions; a graceful Ctrl+C hands over immediately

To check the consumer without the Java backend: `uv run python -m scripts.kafka_producer_test`
(writes a file into the upload folder, records the upload, and publishes a contract-compliant event; it
creates a real "Kafka smoke test" SOP each time, see the script's docstring for how to remove them).

## Creating the Kafka topics

```bash
uv run python -m scripts.init_topics
```

Idempotent: existing topics are skipped. Every topic the project needs is listed in the script's `TOPICS`;
on a new machine or after `docker compose down -v`, run it once instead of typing `kafka-topics.sh` by hand.

## Mock data

```bash
uv run python -m scripts.generate_mock_data
```

Reproducible: `random` and Faker are seeded and every date is computed from a fixed `AS_OF` date, so every
run produces the same rows (the campaign documents in `campaign_docs/` quote them). It empties and refills
the business tables, and through the foreign keys also uploads / documents / chunks. A campaign that is still
running has no product or customer rows: its export doesn't exist yet, so uploading one is a real `mode=new`
(`data_tables/` has samples for campaign 11).

## Tests

```bash
uv run pytest        # pytest / httpx are in requirements.txt, nothing extra to install
```

Kafka is never needed: the consume loop is tested with fake Consumer / Producer / sleep, without real
backoff waits. `test_ingestion.py` needs the docker compose Postgres: it creates a throwaway database, applies
every migration, runs the pipeline with a fake embedder (no OpenAI calls), and drops the database again; it is
skipped when Postgres isn't running.
`test_events.py` detects cross-language drift: it reads the migrations under `db/migration/` in version order
and asserts that the `DocType` / `TableType` / `ContentKind` / `Source` enums match the corresponding CHECK
constraints. If someone changes the SQL and forgets the Python, the test fails.
One test is skipped on Windows (symlinks need administrator rights); that is expected.
