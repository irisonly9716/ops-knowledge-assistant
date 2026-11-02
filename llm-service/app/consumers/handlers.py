"""What happens to a valid upload event: the ingestion pipeline.

The message is only a notification ("upload X is ready"); the uploads row the backend wrote before
publishing is the source of truth for what the upload is. Its status moves like this:

    received ──> processing ──> done
                     └────────> failed   (recorded by the consumer's on_failure after the dead-letter write)

Three steps with deliberate transaction boundaries:
  1. short transaction: lock the uploads row; done already -> a duplicate, skip; otherwise -> processing
  2. no transaction: read the file, parse, decide the fields, chunk, embed. Embedding is a network call
     that can take seconds and be retried; no database lock is held while it runs
  3. short transaction: lock the row again (a duplicate may have finished in the meantime), write
     documents + doc_chunks + the campaign flags, set done
A data table takes the same three steps with a simpler middle: read the CSV and validate it (data_tables.py);
step 3 replaces the campaign's rows in the table.
Everything a reader of the database can see changes in step 3 at once. A failure before or inside it
leaves no half-written document behind.

Idempotency: Kafka delivers at least once, so the same message can arrive twice. The uploads status is
the deduplication record: a message for an upload that is already done is skipped, and the check runs
again under the lock in step 3, where it can't race with another copy of the message.
"""
import logging
import time
import uuid
from collections.abc import Callable
from pathlib import Path

import psycopg

from app import db
from app.consumers import data_tables
from app.consumers.errors import PermanentError
from app.consumers.events import UploadEvent
from app.consumers.storage import resolve_upload_path
from app.rag import store
from app.rag.chunking import chunk_document, sop_steps
from app.rag.document_fields import document_fields
from app.rag.embeddings import Embedder, embed_in_batches
from app.rag.parsing import DocumentParseError, parse_file
from app.time_budget import TimeBudget

log = logging.getLogger("upload_consumer")

# The most a table write can take: the row lock and the campaign lock, then DELETE and COPY
TABLE_WRITE_WORST_CASE_SECONDS = 2 * db.LOCK_TIMEOUT_SECONDS + 2 * db.STATEMENT_TIMEOUT_SECONDS


class UploadHandler:
    def __init__(self, embedder: Embedder, *, connect: Callable[[], psycopg.Connection] = db.connect,
                 upload_dir: str | Path | None = None) -> None:
        self._embedder = embedder
        self._connect = connect
        self._upload_dir = upload_dir

    def __call__(self, event: UploadEvent, budget: TimeBudget | None = None) -> None:
        upload_id = _checked_uuid(event.upload_id)
        started = time.perf_counter()
        with self._connect() as conn:
            # ---- step 1: claim the upload
            with conn.transaction():
                upload = store.lock_upload(conn, upload_id)
                if upload is None:
                    raise PermanentError(f"no uploads row for upload_id={upload_id}; "
                                         "uploads are recorded by the backend before they are published")
                if upload.status == "done":
                    log.info("upload_id=%s is already done; duplicate message skipped", upload_id)
                    return
                if upload.status not in ("received", "processing", "failed"):
                    raise PermanentError(f"upload_id={upload_id} is {upload.status}; only approved uploads are processed")
                store.set_status(conn, upload_id, "processing")
            log.info("upload_id=%s processing: %s %s, department=%s, file=%s", upload_id, upload.content_kind,
                     upload.doc_type or "", upload.department, upload.storage_key)

            path = resolve_upload_path(upload.storage_key, self._upload_dir)
            if upload.content_kind == "data_table":
                self._ingest_table(conn, upload, path, started, budget)
                return

            # ---- step 2: everything slow, outside any transaction
            try:
                parsed = parse_file(path)
            except DocumentParseError as e:
                raise PermanentError(f"cannot parse {upload.storage_key}: {e}") from e

            entry = store.catalog_entry(conn, upload.doc_type, upload.title, upload.department, upload.campaign_id)
            # SOP titles come from the upload; plan and review titles are generated from the campaign
            title = upload.title or f"{entry.campaign_name} - {entry.type_label}"
            fields, warnings = document_fields(title=title, department=upload.department, category=upload.category,
                                               frontmatter=parsed.metadata)
            for warning in warnings:
                log.warning("upload_id=%s %s", upload_id, warning)
            steps = sop_steps(parsed) if upload.doc_type == "sop" else []
            chunks = chunk_document(parsed, entry.document_info(fields.title))
            log.info("upload_id=%s parsed %d elements into %d chunks (%d tokens)", upload_id, len(parsed.elements),
                     len(chunks), sum(c.token_count for c in chunks))

            embed_started = time.perf_counter()
            embedded = embed_in_batches(self._embedder, [c.content for c in chunks], budget=budget)
            log.info("upload_id=%s embedded %d chunks with %s: %d tokens billed, %.2f s", upload_id, len(chunks),
                     self._embedder.model, embedded.total_tokens, time.perf_counter() - embed_started)

            # ---- step 3: write everything at once
            try:
                with conn.transaction():
                    current = store.lock_upload(conn, upload_id)
                    if current.status == "done":
                        log.info("upload_id=%s was finished by a duplicate meanwhile; nothing written", upload_id)
                        return
                    doc_id, replaced = store.save_document(conn, upload, fields, steps, chunks, embedded.vectors,
                                                           self._embedder.model)
            except store.DocumentConflict as e:
                raise PermanentError(str(e)) from e
        log.info("upload_id=%s done: doc_id=%s (%s, %r) with %d chunks in %.2f s", upload_id, doc_id,
                 "replaced" if replaced else "new", fields.title, len(chunks), time.perf_counter() - started)

    def _ingest_table(self, conn: psycopg.Connection, upload: store.UploadRow, path: Path, started: float,
                      budget: TimeBudget | None) -> None:
        try:
            rows = data_tables.parse_table(upload.table_type, path.read_bytes(), upload.campaign_id)
        except data_tables.TableValidationError as e:
            raise PermanentError(f"cannot load {upload.storage_key} into {upload.table_type}: {e}") from e
        log.info("upload_id=%s validated %d rows for %s", upload.upload_id, len(rows), upload.table_type)
        # A normal write takes seconds, so this only stops a message that already spent its time on a sick database
        if budget is not None and budget.remaining() < TABLE_WRITE_WORST_CASE_SECONDS:
            raise TimeoutError(f"{budget.remaining():.0f} s left of the time budget, a table write may need "
                               f"{TABLE_WRITE_WORST_CASE_SECONDS} s")
        try:
            with conn.transaction():
                current = store.lock_upload(conn, upload.upload_id)
                if current.status == "done":
                    log.info("upload_id=%s was finished by a duplicate meanwhile; nothing written", upload.upload_id)
                    return
                replaced = data_tables.save_rows(conn, upload, rows)
        except data_tables.TableConflict as e:
            raise PermanentError(str(e)) from e
        log.info("upload_id=%s done: campaign %s %s now has %d rows (%d replaced) in %.2f s", upload.upload_id,
                 upload.campaign_id, upload.table_type, len(rows), replaced, time.perf_counter() - started)

    def mark_failed(self, event: UploadEvent, reason: str) -> None:
        """The consumer calls this once a message is dead-lettered, so the upload's status says failed."""
        try:
            upload_id = str(uuid.UUID(event.upload_id))
        except ValueError:
            return   # not an upload id at all; there is no row to mark
        with self._connect() as conn:
            store.mark_failed(conn, upload_id, reason)


def _checked_uuid(value: str) -> str:
    try:
        return str(uuid.UUID(value))
    except ValueError:
        raise PermanentError(f"uploadId is not a UUID: {value!r}") from None
