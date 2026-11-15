"""A question's audit row (app/audit.py, migration V13) in a throwaway database: what an answer records, what a question
that brought nothing back records, the row's bounds, and who can read the audit and the feedback."""
import asyncio
import time
from datetime import date
from decimal import Decimal
from uuid import uuid4

import psycopg
import pytest
from postgres_helpers import connect_as, login_in, postgres_available, throwaway_database
from psycopg_pool import PoolTimeout

from app import audit, db
from app.audit import AuditLog, AuditRecord
from app.config import settings
from app.query_flow import FlowAnswer, FlowTrace
from app.rag.answering import ANSWERED, Citation
from app.rag.retrieval import RetrievedChunk
from app.router.rewriting import CampaignReference, Rewritten
from app.router.routing import BOTH, Routing
from app.sql_agent.campaigns import Campaign, Found
from app.sql_agent.execute import QueryResult
from app.sql_agent.pipeline import FAILED, FAILED_TEXT, Attempt, SqlAnswer, SqlTrace

SQL = "SELECT campaign_name, total_gmv FROM campaign_summary WHERE campaign_id = 1"
ROWS = QueryResult(("campaign_name", "total_gmv"), (("Beauty Black Friday Live", "1200.50"),), truncated=False)
CHUNK = RetrievedChunk(chunk_id=4, doc_id=12, doc_type="campaign_review", title="Review", department="Beauty",
                       heading_path=("Review", "Results"), content="...", score=0.6)
CAMPAIGN = Campaign(1, "Beauty Black Friday Live", "Beauty", date(2025, 11, 18), date(2025, 12, 14))
STAFF, EXECUTIVE = 1, 2


@pytest.fixture(scope="module")
def database():
    if not postgres_available():
        pytest.skip("needs the docker compose Postgres")
    with throwaway_database() as name:
        with db.connect(name) as conn:
            conn.execute("INSERT INTO departments (name) VALUES ('Beauty')")
            conn.execute("INSERT INTO hosts (name, department_id, category) VALUES ('Tommy Walter', 1, 'Beauty')")
            conn.execute("""INSERT INTO campaigns (name, department_id, host_id, start_date, end_date, status,
                                                   planned_operating_cost, actual_operating_cost)
                            VALUES ('Beauty Black Friday Live', 1, 1, '2025-11-18', '2025-12-14', 'finished', 10, 12)""")
            conn.execute("INSERT INTO users (name, department_id, role) VALUES ('Ana', 1, 'staff'), "
                         "('Lee', NULL, 'executive')")
        yield name


@pytest.fixture
async def audit_log(database):
    async with db.audit_pool(database) as pool:
        await pool.wait()
        yield AuditLog(pool, wait_seconds=0.5)


def row_of(database: str, record: AuditRecord) -> dict:
    with db.connect(database) as conn:
        cursor = conn.execute("SELECT * FROM query_audit WHERE query_id = %s", (record.query_id,))
        return dict(zip([c.name for c in cursor.description], cursor.fetchone()))


def record(trace: FlowTrace | None = None, outcome: str = ANSWERED, user_id: int = STAFF) -> AuditRecord:
    role, department = ("staff", "Beauty") if user_id == STAFF else ("executive", None)
    return AuditRecord(uuid4(), user_id, role, department, None, "What did our last Black Friday make?", outcome,
                       2412.6, trace)


def answered_from_both() -> FlowTrace:
    """Rewritten to the last Black Friday, routed to both, answered from the excerpts and the rows together."""
    answer = FlowAnswer(ANSWERED, "It made 1,200.50 [1].", [Citation(1, CHUNK)], ["Beauty", "ALL"],
                        ("documents", "data"), SQL, ROWS, CAMPAIGN)
    rewritten = Rewritten("What did {campaign} make?", CampaignReference(words="Black Friday", department=None,
                                                                          position=1), None, "rewritten", 150, 20)
    data = SqlTrace(None, None, (), (Attempt("SELECT 1", None, "rejected", 900.0, 0.0), Attempt(SQL, SQL, None, 800.0, 3.0)),
                    0.0, 1.0, 0.0, (300, 30), (0, 0))
    return FlowTrace(answer, None, Routing("both", 0.97, "Because.", BOTH, "model", 300, 25), "both", None, data, (),
                     110.0, 1250.4, 2100.0, 1900.2, (200, 40), 4001.0, rewritten,
                     "What did Beauty Black Friday Live (the campaign that ran from 2025-11-18 to 2025-12-14) make?",
                     Found(CAMPAIGN, (), ()), 1020.0, True)


# ---- what a row holds


@pytest.mark.anyio
async def test_an_answer_from_both_records_its_route_its_query_its_campaign_and_what_it_cost(database, audit_log):
    written = record(answered_from_both())
    await audit_log.write(written)
    row = row_of(database, written)
    assert (row["user_id"], row["role"], row["department"], row["outcome"]) == (STAFF, "staff", "Beauty", ANSWERED)
    assert row["question"] == "What did our last Black Friday make?" and row["cross_department"] is False
    assert row["rewritten"].startswith("What did Beauty Black Friday Live (the campaign that ran from 2025-11-18")
    assert (row["route"], row["decided_by"], row["confidence"], row["paths"], row["written_by"]) == (
        "both", "model", pytest.approx(0.97), ["documents", "data"], "both")
    assert (row["sql"], row["sql_attempts"], row["result_rows"], row["campaign_id"]) == (SQL, 2, 1, 1)
    assert row["cited_doc_ids"] == [12] and row["cache_hit"] is None
    # The router, the rewrite, two queries written and the answer from both: the documents path was cut short here
    assert row["model_calls"] == 5 and (row["input_tokens"], row["output_tokens"]) == (950, 115)
    assert row["tokens"] == {"router": [300, 25], "rewriter": [150, 20], "writer": [300, 30], "compose": [200, 40]}
    assert row["cost_usd"] == Decimal("0.00015250")   # 950 x $0.10 and 115 x $0.50 a million, all gpt-6-luna
    assert row["total_ms"] == 2413
    assert row["timings_ms"] == {"embed": 110, "rewrite": 1020, "route": 1250, "paths": 2100, "compose": 1900,
                                 "flow": 4001}


@pytest.mark.anyio
async def test_an_executives_question_is_marked_as_reading_across_departments(database, audit_log):
    written = record(answered_from_both(), user_id=EXECUTIVE)
    await audit_log.write(written)
    row = row_of(database, written)
    assert (row["role"], row["department"], row["cross_department"]) == ("executive", None, True)


@pytest.mark.anyio
async def test_a_question_that_brought_nothing_back_records_who_asked_and_leaves_what_it_spent_unknown(database,
                                                                                                     audit_log):
    written = record(None, outcome=audit.TIMEOUT)
    await audit_log.write(written)
    row = row_of(database, written)
    assert (row["outcome"], row["question"], row["total_ms"]) == ("timeout", "What did our last Black Friday make?", 2413)
    assert (row["route"], row["paths"], row["sql"], row["cited_doc_ids"]) == (None, [], None, [])
    # Calls may have finished before the time ran out: unknown, not nothing
    assert (row["model_calls"], row["input_tokens"], row["cost_usd"], row["timings_ms"]) == (None, None, None, None)


@pytest.mark.anyio
async def test_a_failed_data_question_records_the_last_query_it_tried(database, audit_log):
    data = SqlTrace(SqlAnswer(FAILED, FAILED_TEXT, None, None, "Beauty"), None, (),
                    (Attempt("SELECT nope", None, "rejected", 900.0, 0.0), Attempt("SELECT nope2", None, "rejected", 900.0, 0.0)),
                    0.0, 1.0, 0.0, (600, 60), (0, 0))
    answer = FlowAnswer(FAILED, FAILED_TEXT, [], ["Beauty"], ("data",), None, None)
    written = record(FlowTrace(answer, None, None, "data", None, data, (), 0.0, 0.0, 0.0, 0.0, (0, 0), 0.0),
                     outcome=FAILED)
    await audit_log.write(written)
    row = row_of(database, written)
    assert (row["outcome"], row["sql"], row["sql_attempts"], row["result_rows"]) == ("failed", "SELECT nope2", 2, None)


@pytest.mark.anyio
async def test_a_cache_hit_records_where_it_came_from_and_the_paths_its_answer_rests_on(database, audit_log):
    answer = FlowAnswer(ANSWERED, "Do this [1].", [Citation(1, CHUNK)], ["Beauty", "ALL"], ("documents",), None, None)
    hit = FlowTrace(answer, None, None, "cache", None, None, (), 0.0, 0.0, 0.0, 0.0, (0, 0), 3.2, cached="as asked")
    written = record(hit)
    await audit_log.write(written)
    row = row_of(database, written)
    assert (row["cache_hit"], row["written_by"], row["paths"], row["cited_doc_ids"]) == ("as_asked", "cache",
                                                                                        ["documents"], [12])
    assert (row["model_calls"], row["cost_usd"], row["tokens"], row["timings_ms"]) == (0, Decimal(0), None, {"flow": 3})


@pytest.mark.anyio
async def test_a_model_without_a_price_leaves_the_cost_unknown_and_the_row_still_written(database, audit_log,
                                                                                        monkeypatch):
    monkeypatch.setattr(settings, "router_model", "a-model-with-no-price")
    written = record(answered_from_both())
    await audit_log.write(written)
    row = row_of(database, written)
    assert row["cost_usd"] is None and row["input_tokens"] == 950


# ---- its bounds


@pytest.mark.anyio
async def test_the_audit_writes_on_a_pool_of_its_own_because_the_query_paths_is_read_only(database, audit_log):
    async with db.query_pool(database) as pool:
        async with pool.connection() as conn:
            with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
                await conn.execute("INSERT INTO query_audit (query_id, user_id, role, outcome, total_ms) "
                                   "VALUES (%s, 1, 'staff', 'answered', 1)", (uuid4(),))
    await audit_log.write(record())   # the audit's own


@pytest.mark.anyio
async def test_a_row_that_cant_be_written_is_an_error_for_the_caller_not_a_silence(database, audit_log):
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        await audit_log.write(record(user_id=99))


@pytest.mark.anyio
async def test_waiting_for_the_audits_connection_is_bounded(database):
    async with db.audit_pool(database) as pool:
        await pool.wait()
        log = AuditLog(pool, wait_seconds=0.2)
        async with pool.connection():   # the only connection, taken
            started = time.perf_counter()
            with pytest.raises(PoolTimeout):
                await log.write(record())
            assert time.perf_counter() - started < 1


@pytest.mark.anyio
async def test_the_insert_is_bounded_by_the_connections_statement_limit(database, audit_log):
    async with db.audit_pool(database) as pool:
        async with pool.connection() as conn:
            limit = (await (await conn.execute("SHOW statement_timeout")).fetchone())[0]
    assert limit == f"{db.AUDIT_STATEMENT_TIMEOUT_SECONDS}s"


# ---- who can read it


@pytest.mark.parametrize("table", ["query_audit", "query_feedback"])
def test_sql_reader_can_read_neither_the_audit_nor_the_feedback(database, table):
    with login_in("sql_reader") as login, connect_as(database, login) as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute(f"SELECT count(*) FROM {table}")   # a constant from the parameters above


# ---- the feedback table's own rules


@pytest.mark.parametrize(("rating", "comment", "error"), [
    ("meh", None, psycopg.errors.CheckViolation),
    ("down", "x" * 1001, psycopg.errors.CheckViolation),
])
def test_feedback_is_up_or_down_with_a_comment_of_at_most_1000_characters(database, rating, comment, error):
    query_id = uuid4()
    with db.connect(database) as conn:
        conn.execute("INSERT INTO query_audit (query_id, user_id, role, outcome, total_ms) VALUES (%s, 1, 'staff', "
                     "'answered', 1)", (query_id,))
        conn.execute("INSERT INTO query_feedback (query_id, rating, comment) VALUES (%s, 'up', %s)",
                     (query_id, "x" * 1000))
        conn.commit()
        with pytest.raises(error):
            conn.execute("UPDATE query_feedback SET rating = %s, comment = %s WHERE query_id = %s",
                         (rating, comment, query_id))


def test_feedback_must_name_a_question_that_was_recorded(database):
    with db.connect(database) as conn, pytest.raises(psycopg.errors.ForeignKeyViolation):
        conn.execute("INSERT INTO query_feedback (query_id, rating) VALUES (%s, 'up')", (uuid4(),))


@pytest.mark.anyio
async def test_writes_from_many_questions_at_once_share_the_one_connection(database, audit_log):
    records = [record(answered_from_both()) for _ in range(20)]
    await asyncio.gather(*(audit_log.write(r) for r in records))
    with db.connect(database) as conn:
        found = conn.execute("SELECT count(*) FROM query_audit WHERE query_id = ANY(%s)",
                             ([r.query_id for r in records],)).fetchone()[0]
    assert found == 20
