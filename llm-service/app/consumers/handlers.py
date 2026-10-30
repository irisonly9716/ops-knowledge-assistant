"""What happens to a valid upload event: the document ingestion pipeline.

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
from app.consumers.errors import PermanentError
from app.consumers.events import UploadEvent
from app.consumers.storage import resolve_upload_path
from app.rag import store
from app.rag.chunking import chunk_document, sop_steps
from app.rag.document_fields import document_fields
from app.rag.embeddings import Embedder, embed_in_batches
from app.rag.parsing import DocumentParseError, parse_file

log = logging.getLogger("upload_consumer")


class UploadHandler:
    def __init__(self, embedder: Embedder, *, connect: Callable[[], psycopg.Connection] = db.connect,
                 upload_dir: str | Path | None = None) -> None:
        self._embedder = embedder
        self._connect = connect
        self._upload_dir = upload_dir

    def __call__(self, event: UploadEvent) -> None:
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

            if upload.content_kind != "document":
                raise PermanentError("data table ingestion is not implemented yet")

            # ---- step 2: everything slow, outside any transaction
            path = resolve_upload_path(upload.storage_key, self._upload_dir)
            try:
                parsed = parse_file(path)
            except DocumentParseError as e:
                raise PermanentError(f"cannot parse {upload.storage_key}: {e}") from e

            entry = store.catalog_entry(conn, upload.doc_type, upload.title, upload.department, upload.campaign_id)
            # SOP titles come from the upload; plan and review titles are generated from the campaign
            title = upload.title or f"{entry.campaign_name} - {entry.type_label}"
            fields, warnings = document_fields(title=title, department=upload.department, category=event.category,
                                               frontmatter=parsed.metadata)
            for warning in warnings:
                log.warning("upload_id=%s %s", upload_id, warning)
            steps = sop_steps(parsed) if upload.doc_type == "sop" else []
            chunks = chunk_document(parsed, entry.document_info(fields.title))
            log.info("upload_id=%s parsed %d elements into %d chunks (%d tokens)", upload_id, len(parsed.elements),
                     len(chunks), sum(c.token_count for c in chunks))

            embed_started = time.perf_counter()
            embedded = embed_in_batches(self._embedder, [c.content for c in chunks])
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
