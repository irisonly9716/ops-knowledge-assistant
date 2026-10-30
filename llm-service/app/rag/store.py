"""SQL for document ingestion: the uploads row, the catalog labels, documents and doc_chunks.

The functions take an open connection and never commit themselves: where a transaction starts and
ends is the caller's decision (see app/consumers/handlers.py).
"""
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from app.rag.chunking import Chunk, DocumentInfo
from app.rag.document_fields import DocumentFields


class DocumentConflict(Exception):
    """The document can't be written as the upload asks: a document with the same natural key already
    exists, or the version this upload was meant to replace has been replaced in the meantime.
    Retrying gives the same answer."""


@dataclass(frozen=True)
class UploadRow:
    upload_id: str
    status: str
    content_kind: str
    doc_type: str | None
    department: str
    campaign_id: int | None
    title: str | None
    storage_key: str
    replaces_upload_id: str | None


@dataclass(frozen=True)
class CatalogEntry:
    type_label: str
    department_label: str
    campaign_name: str | None
    start_date: date | None
    host_name: str | None
    display_name: str

    def document_info(self, title: str) -> DocumentInfo:
        return DocumentInfo(type_label=self.type_label, department_label=self.department_label, title=title,
                            campaign_name=self.campaign_name, start_date=self.start_date, host_name=self.host_name)


def lock_upload(conn: psycopg.Connection, upload_id: str) -> UploadRow | None:
    """The uploads row, locked until the surrounding transaction ends (FOR UPDATE): a second consumer
    handling a duplicate of the same message waits here instead of working on the same upload at once."""
    row = conn.execute(
        """SELECT upload_id::text, status, content_kind, doc_type, department, campaign_id, title,
                  storage_key, replaces_upload_id::text
           FROM uploads WHERE upload_id = %s FOR UPDATE""",
        (upload_id,),
    ).fetchone()
    return UploadRow(*row) if row else None


def set_status(conn: psycopg.Connection, upload_id: str, status: str, error: str | None = None) -> None:
    conn.execute("UPDATE uploads SET status = %s, error = %s, updated_at = now() WHERE upload_id = %s",
                 (status, error, upload_id))


def mark_failed(conn: psycopg.Connection, upload_id: str, error: str) -> None:
    # Only an upload that was on its way can fail. A done upload stays done (a late duplicate failing
    # must not undo it); pending_approval / rejected were never meant to be processed.
    conn.execute(
        """UPDATE uploads SET status = 'failed', error = %s, updated_at = now()
           WHERE upload_id = %s AND status IN ('received', 'processing', 'failed')""",
        (error, upload_id),
    )


def catalog_entry(conn: psycopg.Connection, doc_type: str, title: str | None, department: str,
                  campaign_id: int | None) -> CatalogEntry:
    """The document_catalog labels for a document that may not be written yet (catalog_entry() in V5)."""
    row = conn.execute("SELECT * FROM catalog_entry(%s, %s, %s, %s)",
                       (doc_type, title, department, campaign_id)).fetchone()
    return CatalogEntry(*row)


def save_document(conn: psycopg.Connection, upload: UploadRow, fields: DocumentFields, steps: list[dict[str, Any]],
                  chunks: Sequence[Chunk], vectors: Sequence[Sequence[float]], embedding_model: str) -> tuple[int, bool]:
    """Write documents + doc_chunks for this upload and mark it done. Returns (doc_id, replaced).

    New document: INSERT. The natural-key unique constraints (SOP: department + normalized title; plan /
    review: campaign + type) turn a second "new" upload of the same document into DocumentConflict.
    Replacement: UPDATE the row that the replaced upload produced, keeping its doc_id, then delete its
    chunks and insert the new ones. If no row belongs to replaces_upload_id any more, someone replaced
    the document while this upload waited (optimistic concurrency): DocumentConflict.
    """
    values = (upload.doc_type, fields.title, fields.department, upload.campaign_id, fields.category,
              Jsonb(steps), fields.responsible_team, upload.storage_key, upload.upload_id)
    replaced = upload.replaces_upload_id is not None
    if replaced:
        row = conn.execute(
            """UPDATE documents SET doc_type = %s, title = %s, department = %s, campaign_id = %s, category = %s,
                      steps = %s, responsible_team = %s, source_path = %s, upload_id = %s
               WHERE upload_id = %s RETURNING doc_id""",
            (*values, upload.replaces_upload_id),
        ).fetchone()
        if row is None:
            raise DocumentConflict(f"the version this upload replaces ({upload.replaces_upload_id}) is no longer "
                                   "the current one; it was replaced in the meantime")
        doc_id = row[0]
        conn.execute("DELETE FROM doc_chunks WHERE doc_id = %s", (doc_id,))
    else:
        try:
            doc_id = conn.execute(
                """INSERT INTO documents (doc_type, title, department, campaign_id, category, steps,
                                          responsible_team, source_path, upload_id)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING doc_id""",
                values,
            ).fetchone()[0]
        except psycopg.errors.UniqueViolation as e:
            raise DocumentConflict(f"this document already exists ({e.diag.constraint_name}); "
                                   "upload it as a replacement instead") from e

    with conn.cursor() as cur:
        cur.executemany(
            """INSERT INTO doc_chunks (doc_id, department, chunk_index, content, embedding, embedding_model, metadata)
               VALUES (%s, %s, %s, %s, %s::vector, %s, %s)""",
            [(doc_id, fields.department, chunk.index, chunk.content, _vector_literal(vector), embedding_model,
              Jsonb(chunk.metadata())) for chunk, vector in zip(chunks, vectors, strict=True)],
        )

    # The shortcut flags on campaigns (see V2): set in the same transaction, so they can't disagree with documents
    if upload.doc_type == "campaign_plan":
        conn.execute("UPDATE campaigns SET has_plan_doc = TRUE WHERE campaign_id = %s", (upload.campaign_id,))
    elif upload.doc_type == "campaign_review":
        conn.execute("UPDATE campaigns SET has_review_doc = TRUE WHERE campaign_id = %s", (upload.campaign_id,))

    set_status(conn, upload.upload_id, "done")
    return doc_id, replaced


def _vector_literal(vector: Sequence[float]) -> str:
    """pgvector's text format, "[0.1,0.2,...]", cast with ::vector in the SQL. Avoids the pgvector Python
    package, which would pull in numpy for this one conversion."""
    return "[" + ",".join(repr(float(x)) for x in vector) + "]"
