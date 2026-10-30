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
├── consumers/     Kafka consumer (the document pipeline), a separate worker process
│   ├── upload_consumer.py   consume loop: manual commits + retries + dead-letter topic + graceful shutdown; entry point main()
│   ├── events.py            the Python half of the message contract (pydantic, mirrors Java's UploadEvent)
│   ├── storage.py           storageKey -> the real file in the upload folder (with path traversal defense)
│   ├── handlers.py          what happens to a valid event (parsing/chunking/embedding/storing are added step by step)
│   └── errors.py            PermanentError: errors where retrying is pointless, straight to the dead-letter topic
├── rag/           parsing / chunking / embedding / retrieval / Q&A
│   ├── parsing/             Markdown / docx / PDF -> the same typed elements (headings, paragraphs, list items, tables)
│   └── embeddings.py        Embedder interface + OpenAI implementation
├── sql_agent/     Text-to-SQL + safety checks + row-level filtering
├── router/        query routing + combined answers
└── memory/        Redis cache + conversation memory
tests/             pytest; test_events (contract / cross-language drift) test_storage (path traversal)
                   test_upload_consumer (every branch of the consume loop) test_health
                   test_embeddings (fake OpenAI client) test_parsing (all three formats parse the same)
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

## Running the consumer

```bash
uv run python -m app.consumers.upload_consumer
```

**It is a process separate from FastAPI** (`uvicorn app.main:app` is the other one). Prerequisites: Kafka
running and the topics created (`init_topics`, which includes the dead-letter topic
`document-upload-events.dlq`). The startup log prints the resolved `upload_dir`: mismatched upload folders
(the two services pointing at different directories) is the most common deployment problem.

Behavior (design reasons in [docs/architecture.md](../docs/architecture.md)):
- **at least once**: auto-commit off; the offset is committed only after processing (or once the message is safely dead-lettered)
- broken message (not JSON / breaks the contract / path outside the upload folder) → no retry, straight to the dead-letter topic `document-upload-events.dlq`
- any other exception → exponential backoff (3 attempts in total by default = first try + 2 retries, waiting 0.5 s, 1 s), then the dead-letter topic; dead letters carry the failure reason, attempt count and source offset
- dead-letter write fails → no commit, and the process crashes (better to stop than to lose a message)
- after being killed hard, a restarted consumer waits for the old member's **session timeout** (about 45 seconds) before taking over the partitions; a graceful Ctrl+C hands over immediately

To check the consumer without the Java backend: `uv run python -m scripts.kafka_producer_test`
(writes a file into the upload folder and publishes a contract-compliant event).

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
the business tables, and through the foreign keys also uploads / documents / chunks.

## Tests

```bash
uv run pytest        # pytest / httpx are in requirements.txt, nothing extra to install
```

Everything uses fake Consumer / Producer / sleep: no running Kafka needed, no real backoff waits.
`test_events.py` detects cross-language drift: it reads the migrations under `db/migration/` in version order
and asserts that the `DocType` / `TableType` / `ContentKind` / `Source` enums match the corresponding CHECK
constraints. If someone changes the SQL and forgets the Python, the test fails.
One test is skipped on Windows (symlinks need administrator rights); that is expected.
