"""The ingestion pipeline against a real Postgres: documents + doc_chunks + the uploads status,
idempotency, rollback, replacements and conflicts.

Needs the docker compose Postgres; skipped when it isn't reachable. The module creates a throwaway
database, applies every migration in db/migration/ in order, and drops it at the end, so development
data is never touched. Embeddings come from a fake embedder: no network, no cost.
"""
import logging
import re
import uuid
from pathlib import Path

import psycopg
import pytest

from app import db
from app.consumers.errors import PermanentError
from app.consumers.events import UploadEvent
from app.consumers.handlers import UploadHandler
from app.rag import store
from app.rag.embeddings import EMBEDDING_DIMENSIONS, EmbeddingBatch

REPO_ROOT = Path(__file__).resolve().parents[2]
SOP = REPO_ROOT / "sop_docs" / "account-restriction-handling.md"
REVIEW = REPO_ROOT / "campaign_docs" / "beauty-black-friday-live-review.md"
SOP_TITLE = "Account Restriction Handling"


def _migration_version(path: Path) -> tuple[int, ...]:
    return tuple(int(part) for part in re.split(r"[._]", path.name[1:].split("__", 1)[0]))


MIGRATIONS = sorted((REPO_ROOT / "db" / "migration").glob("V*__*.sql"), key=_migration_version)


def _postgres_available() -> bool:
    try:
        with db.connect() as conn:
            conn.execute("SELECT 1")
        return True
    except psycopg.OperationalError:
        return False


pytestmark = pytest.mark.skipif(not _postgres_available(), reason="needs the docker compose Postgres")


@pytest.fixture(scope="module")
def database():
    name = f"oka_test_{uuid.uuid4().hex[:8]}"
    with db.connect() as admin:
        admin.execute(f'CREATE DATABASE "{name}"')
    try:
        with db.connect(name) as conn:
            for path in MIGRATIONS:
                conn.execute(path.read_text(encoding="utf-8"))
            conn.execute("INSERT INTO departments (name) VALUES ('Beauty'), ('Fresh Grocery')")
            conn.execute("INSERT INTO users (name, department_id, role) VALUES ('Test Staff', 1, 'staff')")
            conn.execute("INSERT INTO hosts (name, department_id) VALUES ('Tommy Walter', 1)")
            conn.execute("""INSERT INTO campaigns (name, department_id, host_id, start_date, end_date, status)
                            VALUES ('Beauty Black Friday Live', 1, 1, '2025-11-18', '2025-12-14', 'finished')""")
        yield name
    finally:
        with db.connect() as admin:
            admin.execute(f'DROP DATABASE "{name}" WITH (FORCE)')


@pytest.fixture
def conn(database):
    with db.connect(database) as connection:
        connection.execute("TRUNCATE uploads, documents, doc_chunks CASCADE")
        connection.execute("UPDATE campaigns SET has_plan_doc = FALSE, has_review_doc = FALSE")
        yield connection


class FakeEmbedder:
    model = "fake-embedding"

    def __init__(self, error: Exception | None = None, during_embed=None):
        self.calls = 0
        self.error = error
        self.during_embed = during_embed   # runs while "the API call" is in flight

    def embed(self, texts: list[str]) -> EmbeddingBatch:
        self.calls += 1
        if self.during_embed:
            self.during_embed()
        if self.error:
            raise self.error
        return EmbeddingBatch(vectors=[[0.001 * (i + 1)] * EMBEDDING_DIMENSIONS for i in range(len(texts))],
                              total_tokens=10 * len(texts))


@pytest.fixture
def make_handler(database, tmp_path):
    def make(embedder=None):
        return UploadHandler(embedder or FakeEmbedder(), connect=lambda: db.connect(database), upload_dir=tmp_path)
    return make


@pytest.fixture
def upload(conn, tmp_path):
    """Records an upload the way the backend does (file in the upload folder + uploads row) and returns the event."""
    def record(source: Path | str, *, doc_type="sop", department="ALL", campaign_id=None, title=SOP_TITLE,
               replaces=None, status="received", content_kind="document", category=None,
               filename="doc.md") -> UploadEvent:
        upload_id = str(uuid.uuid4())
        storage_key = f"{upload_id}-{source.name if isinstance(source, Path) else filename}"
        content = source.read_bytes() if isinstance(source, Path) else source.encode("utf-8")
        (tmp_path / storage_key).write_bytes(content)
        table_type = "campaign_products" if content_kind == "data_table" else None
        doc_type = None if content_kind == "data_table" else doc_type
        conn.execute(
            """INSERT INTO uploads (upload_id, source, uploaded_by, content_kind, doc_type, table_type, department,
                                    campaign_id, title, original_filename, storage_key, replaces_upload_id, status)
               VALUES (%s, 'manual', 1, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (upload_id, content_kind, doc_type, table_type, department, campaign_id,
             title if doc_type == "sop" else None, storage_key, storage_key, replaces, status))
        return UploadEvent(upload_id=upload_id, storage_key=storage_key, content_kind=content_kind, doc_type=doc_type,
                           table_type=table_type, department=department, campaign_id=campaign_id, category=category,
                           source="manual")
    return record


def _status(conn, event) -> tuple[str, str | None]:
    return conn.execute("SELECT status, error FROM uploads WHERE upload_id = %s", (event.upload_id,)).fetchone()


def _counts(conn) -> tuple[int, int]:
    return conn.execute("SELECT (SELECT count(*) FROM documents), (SELECT count(*) FROM doc_chunks)").fetchone()


# ---------------------------------------------------------------------------
# The normal path
# ---------------------------------------------------------------------------
def test_an_sop_is_stored_with_its_chunks_and_the_upload_is_done(conn, upload, make_handler):
    event = upload(SOP)
    make_handler()(event)

    doc = conn.execute("""SELECT doc_id, title, department, category, responsible_team, jsonb_array_length(steps),
                                 source_path FROM documents""").fetchone()
    assert doc[1:] == (SOP_TITLE, "ALL", "Trust & Safety", "Trust & Safety team", 7, event.storage_key)
    chunks = conn.execute("""SELECT chunk_index, department, embedding_model, vector_dims(embedding),
                                    metadata->'heading_path'->>1, left(content, 22)
                             FROM doc_chunks WHERE doc_id = %s ORDER BY chunk_index""", (doc[0],)).fetchall()
    assert chunks == [
        (0, "ALL", "fake-embedding", EMBEDDING_DIMENSIONS, "When to Use", "SOP | All departments\n"),
        (1, "ALL", "fake-embedding", EMBEDDING_DIMENSIONS, "Procedure", "SOP | All departments\n"),
        (2, "ALL", "fake-embedding", EMBEDDING_DIMENSIONS, "Partner Teams", "SOP | All departments\n"),
    ]
    assert _status(conn, event) == ("done", None)


def test_a_campaign_review_gets_a_generated_title_its_header_and_the_campaign_flag(conn, upload, make_handler):
    event = upload(REVIEW, doc_type="campaign_review", department="Beauty", campaign_id=1)
    make_handler()(event)

    assert conn.execute("SELECT title, steps FROM documents").fetchone() == (
        "Beauty Black Friday Live - Post-campaign review", [])
    first_chunk = conn.execute("SELECT content FROM doc_chunks WHERE chunk_index = 0").fetchone()[0]
    assert first_chunk.startswith("Post-campaign review | Beauty | Beauty Black Friday Live | 2025-11-18 | "
                                  "Host: Tommy Walter\n")
    assert conn.execute("SELECT has_review_doc, has_plan_doc FROM campaigns").fetchone() == (True, False)
    assert conn.execute("SELECT display_name FROM document_catalog").fetchone()[0] == \
        "Review_2025-11-18_Beauty_Tommy Walter_Beauty Black Friday Live"


def test_the_upload_decides_the_department_not_the_frontmatter(conn, upload, make_handler, caplog):
    # The file's frontmatter says department: ALL; it was uploaded for Beauty
    event = upload(SOP, department="Beauty")
    with caplog.at_level(logging.WARNING, logger="upload_consumer"):
        make_handler()(event)

    assert conn.execute("SELECT department FROM documents").fetchone()[0] == "Beauty"
    assert conn.execute("SELECT DISTINCT department FROM doc_chunks").fetchall() == [("Beauty",)]
    assert "frontmatter department 'ALL' ignored; the upload says 'Beauty'" in caplog.text


# ---------------------------------------------------------------------------
# Idempotency: the same message twice writes once
# ---------------------------------------------------------------------------
def test_the_same_message_twice_writes_once(conn, upload, make_handler):
    event = upload(SOP)
    embedder = FakeEmbedder()
    handler = make_handler(embedder)

    handler(event)
    handler(event)   # Kafka redelivered it

    assert _counts(conn) == (1, 3)
    assert embedder.calls == 1   # the duplicate is skipped before any work, not just before the write


def test_a_duplicate_that_finishes_first_makes_the_other_write_nothing(conn, upload, make_handler, database):
    # While this copy is embedding, another copy of the message finishes the upload. The second check,
    # under the lock right before writing, must notice and write nothing.
    event = upload(SOP)

    def other_copy_finishes():
        with db.connect(database) as other:
            other.execute("UPDATE uploads SET status = 'done' WHERE upload_id = %s", (event.upload_id,))

    make_handler(FakeEmbedder(during_embed=other_copy_finishes))(event)

    assert _counts(conn) == (0, 0)


# ---------------------------------------------------------------------------
# Failures leave nothing half-written
# ---------------------------------------------------------------------------
def test_a_failure_while_embedding_leaves_nothing_behind(conn, upload, make_handler):
    event = upload(SOP)
    handler = make_handler(FakeEmbedder(error=ConnectionError("embedding API down")))

    with pytest.raises(ConnectionError):
        handler(event)   # an ordinary error: the consumer will retry it

    assert _counts(conn) == (0, 0)
    assert _status(conn, event) == ("processing", None)
    handler.mark_failed(event, "ConnectionError: embedding API down")   # what the consumer does after the last attempt
    assert _status(conn, event) == ("failed", "ConnectionError: embedding API down")


def test_a_failure_in_the_middle_of_the_write_rolls_everything_back(conn, upload, make_handler, monkeypatch):
    # The documents row is already inserted when writing the chunks fails: the transaction must take it back
    event = upload(SOP)

    def broken(vector):
        raise RuntimeError("disk full")
    monkeypatch.setattr(store, "_vector_literal", broken)

    with pytest.raises(RuntimeError, match="disk full"):
        make_handler()(event)

    assert _counts(conn) == (0, 0)
    assert _status(conn, event)[0] == "processing"


def test_a_failed_upload_can_be_processed_again(conn, upload, make_handler):
    # A message replayed from the dead-letter topic after the cause was fixed
    event = upload(SOP, status="failed")
    make_handler()(event)
    assert _status(conn, event) == ("done", None)


# ---------------------------------------------------------------------------
# Replacements and conflicts
# ---------------------------------------------------------------------------
def test_a_replacement_keeps_the_doc_id_and_rebuilds_the_chunks(conn, upload, make_handler):
    handler = make_handler()
    first = upload(SOP)
    handler(first)
    doc_id, old_chunk_ids = conn.execute(
        "SELECT doc_id, array_agg(chunk_id) FROM doc_chunks GROUP BY doc_id").fetchone()

    new_version = SOP.read_text(encoding="utf-8").replace(
        "Applies to every department.", "Applies to every department, including partner shops.")
    second = upload(new_version, replaces=first.upload_id, filename="account-restriction-handling.md")
    handler(second)

    assert conn.execute("SELECT doc_id, upload_id::text FROM documents").fetchall() == [(doc_id, second.upload_id)]
    chunks = conn.execute("SELECT chunk_id, content FROM doc_chunks WHERE doc_id = %s", (doc_id,)).fetchall()
    assert len(chunks) == 3
    assert not {chunk_id for chunk_id, _ in chunks} & set(old_chunk_ids)   # old chunks gone, not mixed in
    assert any("including partner shops" in content for _, content in chunks)


def test_a_replacement_of_a_version_that_is_no_longer_current_is_rejected(conn, upload, make_handler):
    handler = make_handler()
    first = upload(SOP)
    handler(first)
    handler(upload(SOP, replaces=first.upload_id))
    stale = upload(SOP, replaces=first.upload_id)   # also asked to replace the first version

    with pytest.raises(PermanentError, match="no longer the current one"):
        handler(stale)
    assert _counts(conn) == (1, 3)


def test_a_second_new_upload_of_the_same_sop_is_a_conflict(conn, upload, make_handler):
    handler = make_handler()
    handler(upload(SOP))
    # same department, same title up to case and spaces: the natural key says it is the same document
    duplicate = upload(SOP, title="  account restriction handling ")

    with pytest.raises(PermanentError, match="uq_documents_sop_title"):
        handler(duplicate)
    assert _counts(conn) == (1, 3)


# ---------------------------------------------------------------------------
# Messages that can never succeed
# ---------------------------------------------------------------------------
def test_an_unparseable_file_is_a_permanent_error(conn, upload, make_handler):
    event = upload("plain text", filename="notes.txt")
    with pytest.raises(PermanentError, match="unsupported file type .txt"):
        make_handler()(event)


def test_a_message_without_an_uploads_row_is_a_permanent_error(make_handler, conn):
    event = UploadEvent(upload_id=str(uuid.uuid4()), storage_key="x.md", content_kind="document", doc_type="sop",
                        department="ALL", source="manual")
    with pytest.raises(PermanentError, match="no uploads row"):
        make_handler()(event)


def test_an_upload_waiting_for_approval_is_not_processed_and_stays_waiting(conn, upload, make_handler):
    event = upload(SOP, status="pending_approval")
    handler = make_handler()
    with pytest.raises(PermanentError, match="pending_approval"):
        handler(event)
    handler.mark_failed(event, "should not apply")
    assert _status(conn, event) == ("pending_approval", None)


def test_data_tables_are_not_ingested_yet(conn, upload, make_handler):
    event = upload("product,units\n", content_kind="data_table", department="Beauty", campaign_id=1,
                   filename="products.csv")
    with pytest.raises(PermanentError, match="not implemented"):
        make_handler()(event)
