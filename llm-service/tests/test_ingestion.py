"""The ingestion pipeline against a real Postgres: documents + doc_chunks + the uploads status,
idempotency, rollback, replacements and conflicts; the same for data tables.

Needs the docker compose Postgres; skipped when it isn't reachable. The module creates a throwaway
database, applies every migration in db/migration/ in order, and drops it at the end, so development
data is never touched. Embeddings come from a fake embedder: no network, no cost.
"""
import logging
import re
import threading
import uuid
from decimal import Decimal
from pathlib import Path

import psycopg
import pytest

from app import db
from app.consumers import data_tables
from app.consumers.errors import PermanentError
from app.consumers.events import UploadEvent
from app.consumers.handlers import TABLE_WRITE_WORST_CASE_SECONDS, UploadHandler
from app.rag import store
from app.rag.embeddings import EMBEDDING_DIMENSIONS, EmbeddingBatch
from app.time_budget import TimeBudget

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
                            VALUES ('Beauty Black Friday Live', 1, 1, '2025-11-18', '2025-12-14', 'finished'),
                                   ('Beauty Weekly Live', 1, 1, '2026-10-01', '2026-10-31', 'running')""")
        yield name
    finally:
        with db.connect() as admin:
            admin.execute(f'DROP DATABASE "{name}" WITH (FORCE)')


@pytest.fixture
def conn(database):
    with db.connect(database) as connection:
        connection.execute("TRUNCATE uploads, documents, doc_chunks, campaign_products, "
                           "campaign_customer_behavior CASCADE")
        connection.execute("UPDATE campaigns SET has_plan_doc = FALSE, has_review_doc = FALSE")
        yield connection


class FakeEmbedder:
    model = "fake-embedding"

    def __init__(self, error: Exception | None = None, during_embed=None):
        self.calls = 0
        self.error = error
        self.during_embed = during_embed   # runs while "the API call" is in flight

    def embed(self, texts: list[str], budget=None) -> EmbeddingBatch:
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
               filename="doc.md", table_type="campaign_products") -> UploadEvent:
        upload_id = str(uuid.uuid4())
        storage_key = f"{upload_id}-{source.name if isinstance(source, Path) else filename}"
        content = source.read_bytes() if isinstance(source, Path) else source.encode("utf-8")
        (tmp_path / storage_key).write_bytes(content)
        table_type = table_type if content_kind == "data_table" else None
        doc_type = None if content_kind == "data_table" else doc_type
        conn.execute(
            """INSERT INTO uploads (upload_id, source, uploaded_by, content_kind, doc_type, table_type, department,
                                    campaign_id, title, category, original_filename, storage_key, replaces_upload_id,
                                    status)
               VALUES (%s, 'manual', 1, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (upload_id, content_kind, doc_type, table_type, department, campaign_id,
             title if doc_type == "sop" else None, category, storage_key, storage_key, replaces, status))
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


def test_the_category_chosen_at_upload_is_used(conn, upload, make_handler):
    make_handler()(upload(SOP, category="Compliance"))
    assert conn.execute("SELECT category, version FROM documents").fetchone() == ("Compliance", 1)


def test_a_campaign_review_gets_a_generated_title_its_header_and_the_campaign_flag(conn, upload, make_handler):
    event = upload(REVIEW, doc_type="campaign_review", department="Beauty", campaign_id=1)
    make_handler()(event)

    assert conn.execute("SELECT title, steps FROM documents").fetchone() == (
        "Beauty Black Friday Live - Post-campaign review", [])
    first_chunk = conn.execute("SELECT content FROM doc_chunks WHERE chunk_index = 0").fetchone()[0]
    assert first_chunk.startswith("Post-campaign review | Beauty | Beauty Black Friday Live | 2025-11-18 | "
                                  "Host: Tommy Walter\n")
    assert conn.execute("SELECT has_review_doc, has_plan_doc FROM campaigns WHERE campaign_id = 1").fetchone() == (
        True, False)
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

    assert conn.execute("SELECT doc_id, upload_id::text, version FROM documents").fetchall() == [
        (doc_id, second.upload_id, 2)]
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


# ---------------------------------------------------------------------------
# Data tables
# ---------------------------------------------------------------------------

PRODUCTS_HEADER = ("product_name,units_sold,sell_through_rate,return_rate,sales_amount,"
                   "click_through_rate,cost_price,profit,profit_margin\n")
SERUM = "Serum,120,0.75,0.02,3600.50,0.031,12.00,2160.50,0.60\n"
LIP_TINT = "Lip Tint,80,0.50,0.01,1600.00,0.020,8.00,960.00,0.60\n"
CUSTOMERS_HEADER = "customer_ref,region,dwell_time_minutes,products_purchased,purchase_amount,ip_address\n"


@pytest.fixture
def table_upload(upload):
    def record(csv_text: str, *, campaign_id=1, replaces=None, table_type="campaign_products") -> UploadEvent:
        return upload(csv_text, content_kind="data_table", department="Beauty", campaign_id=campaign_id,
                      replaces=replaces, table_type=table_type, filename="table.csv")
    return record


def _products(conn, campaign_id=1) -> list[str]:
    rows = conn.execute("SELECT product_name FROM campaign_products WHERE campaign_id = %s ORDER BY product_name",
                        (campaign_id,)).fetchall()
    return [row[0] for row in rows]


def test_a_products_csv_ends_up_as_rows_with_no_document_and_no_embedding(conn, table_upload, make_handler):
    embedder = FakeEmbedder()
    event = table_upload(PRODUCTS_HEADER + SERUM + LIP_TINT)
    make_handler(embedder)(event)

    assert _status(conn, event) == ("done", None)
    assert _products(conn) == ["Lip Tint", "Serum"]
    assert _counts(conn) == (0, 0)
    assert embedder.calls == 0
    row = conn.execute("""SELECT units_sold, sales_amount, profit_margin FROM campaign_products
                          WHERE product_name = 'Serum'""").fetchone()
    assert row == (120, Decimal("3600.50"), Decimal("0.6000"))


def test_a_customers_csv_stores_the_purchased_products_as_a_json_array(conn, table_upload, make_handler):
    event = table_upload(CUSTOMERS_HEADER + "C-1,Northeast,12.5,Serum;Lip Tint,99.90,203.0.113.7\nC-2,,3,,0,\n",
                         table_type="campaign_customer_behavior")
    make_handler()(event)

    rows = conn.execute("""SELECT customer_ref, region, products_purchased, jsonb_array_length(products_purchased)
                           FROM campaign_customer_behavior ORDER BY customer_ref""").fetchall()
    assert rows == [("C-1", "Northeast", ["Serum", "Lip Tint"], 2), ("C-2", None, [], 0)]


def test_a_table_replacement_swaps_the_rows_of_that_campaign_only(conn, table_upload, make_handler):
    other = table_upload(PRODUCTS_HEADER + SERUM, campaign_id=2)
    first = table_upload(PRODUCTS_HEADER + SERUM + LIP_TINT)
    make_handler()(other)
    make_handler()(first)

    second = table_upload(PRODUCTS_HEADER + LIP_TINT, replaces=first.upload_id)
    make_handler()(second)

    assert _products(conn) == ["Lip Tint"]
    assert _products(conn, campaign_id=2) == ["Serum"]


def test_rows_that_came_without_an_upload_can_be_replaced(conn, table_upload, make_handler):
    conn.execute("INSERT INTO campaign_products (campaign_id, product_name) VALUES (1, 'From the mock data')")
    make_handler()(table_upload(PRODUCTS_HEADER + SERUM))
    assert _products(conn) == ["Serum"]


def test_the_same_table_message_twice_writes_once(conn, table_upload, make_handler):
    event = table_upload(PRODUCTS_HEADER + SERUM)
    make_handler()(event)
    conn.execute("DELETE FROM campaign_products")   # so a second write would show

    make_handler()(event)
    assert _products(conn) == []
    assert _status(conn, event)[0] == "done"


def test_a_table_duplicate_that_finishes_first_makes_the_other_write_nothing(conn, table_upload, make_handler,
                                                                             monkeypatch):
    """Without the second check under the lock, the slower copy would see its own upload as a newer version
    and fail an upload that succeeded."""
    event = table_upload(PRODUCTS_HEADER + SERUM)
    real_parse = data_tables.parse_table

    def parse_while_the_other_copy_finishes(*args):
        monkeypatch.setattr(data_tables, "parse_table", real_parse)
        make_handler()(event)
        conn.execute("DELETE FROM campaign_products")   # so a second write would show
        return real_parse(*args)
    monkeypatch.setattr(data_tables, "parse_table", parse_while_the_other_copy_finishes)

    make_handler()(event)

    assert _status(conn, event) == ("done", None)
    assert _products(conn) == []


def test_a_bad_csv_is_a_permanent_error_and_the_current_rows_stay(conn, table_upload, make_handler):
    first = table_upload(PRODUCTS_HEADER + SERUM)
    make_handler()(first)

    bad = table_upload(PRODUCTS_HEADER + LIP_TINT.replace("80", "lots"), replaces=first.upload_id)
    handler = make_handler()
    with pytest.raises(PermanentError, match="row 2, units_sold: 'lots' is not a whole number"):
        handler(bad)
    handler.mark_failed(bad, "reason")

    assert _products(conn) == ["Serum"]
    assert _status(conn, bad)[0] == "failed"


def test_a_table_replacement_of_a_version_that_is_no_longer_current_is_rejected(conn, table_upload, make_handler):
    first = table_upload(PRODUCTS_HEADER + SERUM)
    make_handler()(first)
    make_handler()(table_upload(PRODUCTS_HEADER + LIP_TINT, replaces=first.upload_id))

    stale = table_upload(PRODUCTS_HEADER + SERUM + LIP_TINT, replaces=first.upload_id)
    with pytest.raises(PermanentError, match="based on version"):
        make_handler()(stale)
    assert _products(conn) == ["Lip Tint"]


def test_a_second_new_upload_of_a_table_is_a_conflict(conn, table_upload, make_handler):
    make_handler()(table_upload(PRODUCTS_HEADER + SERUM))
    with pytest.raises(PermanentError, match="based on version"):
        make_handler()(table_upload(PRODUCTS_HEADER + LIP_TINT))
    assert _products(conn) == ["Serum"]


def test_a_failure_in_the_middle_of_the_table_write_rolls_everything_back(conn, table_upload, make_handler, monkeypatch):
    first = table_upload(PRODUCTS_HEADER + SERUM)
    make_handler()(first)
    second = table_upload(PRODUCTS_HEADER + LIP_TINT, replaces=first.upload_id)
    real_set_status = store.set_status

    def fail_when_done(conn_, upload_id, status, *args):
        if status == "done":
            raise RuntimeError("the connection dropped")
        real_set_status(conn_, upload_id, status, *args)
    monkeypatch.setattr(store, "set_status", fail_when_done)

    with pytest.raises(RuntimeError):
        make_handler()(second)

    assert _products(conn) == ["Serum"]   # the DELETE and the INSERT are undone together
    assert _status(conn, second)[0] == "processing"


def test_a_table_write_only_starts_if_its_worst_case_fits_the_time_budget(conn, table_upload, make_handler):
    event = table_upload(PRODUCTS_HEADER + SERUM)

    with pytest.raises(TimeoutError, match="time budget"):
        make_handler()(event, TimeBudget(TABLE_WRITE_WORST_CASE_SECONDS - 1))
    assert _products(conn) == []
    assert _status(conn, event)[0] == "processing"

    make_handler()(event, TimeBudget(TABLE_WRITE_WORST_CASE_SECONDS + 5))
    assert _products(conn) == ["Serum"]


def test_two_uploads_for_one_campaign_take_turns_so_rows_are_never_doubled(conn, table_upload, make_handler, database):
    """Without the lock on the campaign, the second upload would pass the version check while the first one is
    still uncommitted. Customer rows have no unique key to stop it, so both sets of rows would stay."""
    table = "campaign_customer_behavior"
    first = table_upload(CUSTOMERS_HEADER + "C-1,,1,,0,\n", table_type=table)
    second = table_upload(CUSTOMERS_HEADER + "C-2,,1,,0,\n", table_type=table)
    outcome = {}

    def run_second():
        try:
            make_handler()(second)
            outcome["result"] = "done"
        except PermanentError as e:
            outcome["result"] = str(e)

    with db.connect(database) as holder:
        with holder.transaction():
            row = store.lock_upload(holder, first.upload_id)
            data_tables.save_rows(holder, row, [("C-1", None, Decimal(1), None, Decimal(0), None)])
            thread = threading.Thread(target=run_second)
            thread.start()
            thread.join(timeout=1.5)
            assert thread.is_alive()   # waiting for the first upload's transaction
    thread.join(timeout=15)

    assert "based on version" in outcome["result"]
    rows = conn.execute(f"SELECT customer_ref FROM {table}").fetchall()
    assert rows == [("C-1",)]


def test_writing_a_table_does_not_make_new_uploads_for_that_campaign_wait(conn, table_upload, database):
    """A foreign key check takes a light lock on the campaign. FOR UPDATE would block it, FOR NO KEY UPDATE doesn't."""
    event = table_upload(CUSTOMERS_HEADER + "C-1,,1,,0,\n", table_type="campaign_customer_behavior")
    with db.connect(database) as holder, holder.transaction():
        data_tables.save_rows(holder, store.lock_upload(holder, event.upload_id),
                              [("C-1", None, Decimal(1), None, Decimal(0), None)])
        conn.execute("SET lock_timeout = '1s'")
        conn.execute("""INSERT INTO uploads (upload_id, source, uploaded_by, content_kind, table_type, department,
                                             campaign_id, original_filename, storage_key, status)
                        VALUES (%s, 'manual', 1, 'data_table', 'campaign_products', 'Beauty', 1, 'x.csv', %s,
                                'received')""", (str(uuid.uuid4()), str(uuid.uuid4())))


def test_the_extreme_values_validation_lets_through_fit_the_real_columns(conn, table_upload, make_handler):
    """The limits in data_tables.py are copies of the column types; this is where a wrong copy would show."""
    make_handler()(table_upload(
        PRODUCTS_HEADER + "Highest,2147483647,1,1,999999999999.99,1,99999999.99,999999999999.99,9.9999\n"
                          "Lowest,0,0,0,0,0,0,-999999999999.99,-9.9999\n"))
    make_handler()(table_upload(CUSTOMERS_HEADER + "C-1,,9999.99,,99999999.99,\n",
                                table_type="campaign_customer_behavior"))
    assert conn.execute("SELECT count(*) FROM campaign_products").fetchone() == (2,)
    assert conn.execute("SELECT count(*) FROM campaign_customer_behavior").fetchone() == (1,)


@pytest.mark.parametrize(("table", "row", "column"), [
    ("campaign_products", "P,2147483648,1,1,1,1,1,1,1", "units_sold"),
    ("campaign_products", "P,1,1,1,1000000000000,1,1,1,1", "sales_amount"),
    ("campaign_products", "P,1,1,1,999999999999.995,1,1,1,1", "sales_amount"),
    ("campaign_products", "P,1,1,1,1,1,100000000,1,1", "cost_price"),
    ("campaign_products", "P,1,1,1,1,1,1,-1000000000000,1", "profit"),
    ("campaign_products", "P,1,1,1,1,1,1,1,10", "profit_margin"),
    ("campaign_customer_behavior", "C,,10000,,1,", "dwell_time_minutes"),
    ("campaign_customer_behavior", "C,,1,,100000000,", "purchase_amount"),
])
def test_a_value_one_step_past_a_column_limit_is_refused_with_a_clear_message(table, row, column):
    header = PRODUCTS_HEADER if table == "campaign_products" else CUSTOMERS_HEADER
    with pytest.raises(data_tables.TableValidationError, match=f"row 2, {column}: .* is out of range"):
        data_tables.parse_table(table, (header + row).encode(), 1)
