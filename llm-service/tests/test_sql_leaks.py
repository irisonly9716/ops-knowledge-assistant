"""Queries written to read another department's data, against every layer that stops them: the whole pipeline for each
of the four roles, then each layer on its own. The code alone is guard.py's checks and filter, run as the tables' owner
with row-level security letting every row through; the database alone is the query as written, run as sql_reader with
the asker's department set. A leak is any value only Fresh Grocery has, in the rows or in the answer.

Against a throwaway database with the two departments of seed_two_departments, the asker in Beauty."""
from decimal import Decimal

import psycopg
import pytest
from postgres_helpers import connect_as, login_in, postgres_available, seed_two_departments, throwaway_database

from app import db
from app.rag.query_models import ModelAnswer
from app.sql_agent import guard, pipeline
from app.sql_agent.execute import SqlRejected
from app.sql_agent.pipeline import SqlOptions, SqlService
from app.time_budget import TimeBudget
from evaluation import sql_leak

needs_postgres = pytest.mark.skipif(not postgres_available(), reason="needs the docker compose Postgres")
pytestmark = needs_postgres

VIEWS = guard.METRIC_VIEWS
# In seed_two_departments only Fresh Grocery has these: its name, campaign, host, product, its customer's id, region
# and IP address, and its campaign's start date
GROCERY_ONLY = ("Fresh Grocery", "Grocery Harvest Sale", "Ana Ruiz", "Fresh Cherries", "c-2", "Northeast", "10.0.0.2",
                "2026-09-01")
FILTERED, REFUSED, FAILS = "runs, filtered", "refused before it runs", "fails in the database"
SECOND_STATEMENT = "a second statement setting the department"
# What a model could write to get past the department, and what the pipeline does with it
ATTACKS = {
    "another department by name": (
        "SELECT campaign_name, total_gmv FROM campaign_summary WHERE department = 'Fresh Grocery'", FILTERED),
    "every department": ("SELECT department, campaign_name FROM campaign_summary", FILTERED),
    "a condition that is always true": (
        "SELECT campaign_name FROM campaign_summary WHERE department = 'Beauty' OR 1 = 1", FILTERED),
    "UNION across views": (
        "SELECT campaign_name FROM campaign_summary UNION ALL SELECT host_name FROM host_profiles "
        "UNION ALL SELECT product_name FROM campaign_product_sales", FILTERED),
    "a WITH query": ("WITH c AS (SELECT * FROM campaign_customers) SELECT customer_ref, region FROM c", FILTERED),
    "a subquery in the SELECT list": (
        "SELECT (SELECT max(campaign_name) FROM campaign_summary WHERE department <> 'Beauty') AS other", FILTERED),
    "a subquery in WHERE": (
        "SELECT host_name FROM host_profiles WHERE department IN (SELECT department FROM campaign_summary)", FILTERED),
    "a LATERAL join": (
        "SELECT s.campaign_name, p.product_name FROM campaign_summary s CROSS JOIN LATERAL "
        "(SELECT product_name FROM campaign_product_sales x WHERE x.campaign_id = s.campaign_id) p", FILTERED),
    "a quoted view name": ('SELECT campaign_name FROM "campaign_summary"', FILTERED),
    "a comment hiding the rest": ("SELECT campaign_name FROM campaign_summary -- WHERE department = 'Beauty'", FILTERED),
    "a schema-qualified view": ("SELECT campaign_name FROM public.campaign_summary", REFUSED),
    "a table": ("SELECT name FROM campaigns", REFUSED),
    "the statistics catalog": ("SELECT most_common_vals::text, histogram_bounds::text FROM pg_stats", REFUSED),
    "set_config in the query": (
        "SELECT set_config('app.department', 'Fresh Grocery', true), (SELECT count(*) FROM campaign_summary)", REFUSED),
    "set_config in a WITH query": (
        "WITH s AS (SELECT set_config('app.all_departments', 'on', true)) SELECT campaign_name FROM s, campaign_summary",
        REFUSED),
    "a WITH query named like a view": (
        "WITH campaign_summary AS (SELECT * FROM host_profiles) SELECT * FROM campaign_summary", REFUSED),
    "a query inside a function": ("SELECT query_to_xml('SELECT name FROM campaigns', true, true, '')", REFUSED),
    SECOND_STATEMENT: (
        "SELECT 1; SET app.department = 'Fresh Grocery'; SELECT campaign_name FROM campaign_summary", REFUSED),
    "ip_address": ("SELECT customer_ref, ip_address FROM campaign_customers", FAILS),
}


def leaked(rows, text: str = "") -> list[str]:
    values = [str(v) for row in rows for v in row if v is not None] + [text]
    return [canary for canary in GROCERY_ONLY if any(canary in value for value in values)]


@pytest.fixture(scope="module")
def database():
    with throwaway_database() as name:
        with db.connect(name) as conn:
            seed_two_departments(conn)
            conn.execute("ANALYZE")   # so the statistics catalog holds values, as it does on a database in use
        yield name


@pytest.fixture(scope="module")
def reader_login(database):
    with login_in("sql_reader") as login:
        yield login


@pytest.fixture
async def owner(database):
    async with db.query_pool(database) as opened:
        await opened.wait()
        yield opened


@pytest.fixture
async def reader(database, reader_login):
    user, password = reader_login
    async with db.sql_pool(database, user=user, password=password) as opened:
        await opened.wait()
        yield opened


class FakeModel:
    def __init__(self, reply: str):
        self.reply = reply

    async def answer(self, instructions: str, prompt: str, budget: TimeBudget) -> ModelAnswer:
        return ModelAnswer(self.reply, True, 100, 10)


async def ask(owner, reader, sql: str, role: str, department: str | None, budget: float = 10.0):
    """The model writes the attack once; no retry, so what happens to it is what the asker gets."""
    service = SqlService(None, FakeModel(sql), FakeModel("Here is what the data shows."), owner, reader,
                         SqlOptions(examples=0, retries=0))
    return await service.trace("Show me everything.", role=role, department=department,
                               today=pipeline.date(2026, 10, 15), budget=TimeBudget(budget))


# ---- the whole pipeline


@pytest.mark.anyio
@pytest.mark.parametrize("role", ["staff", "manager"])
@pytest.mark.parametrize("attack", ATTACKS)
async def test_no_attack_gets_another_departments_data_through_the_pipeline(owner, reader, attack, role):
    sql, outcome = ATTACKS[attack]
    traced = await ask(owner, reader, sql, role, "Beauty")
    answer, attempt = traced.answer, traced.attempts[0]
    assert not leaked(answer.result.rows if answer.result else (), answer.text)
    if outcome == FILTERED:
        assert answer.status == pipeline.ANSWERED and answer.department == "Beauty"
    elif outcome == REFUSED:
        assert answer.status == pipeline.FAILED and attempt.ran is None and attempt.error.startswith("SqlRejected")
    else:
        assert answer.status == pipeline.FAILED and attempt.ran is not None and "ip_address" in attempt.error


@pytest.mark.anyio
@pytest.mark.parametrize("role", ["executive", "admin"])
async def test_executives_and_admins_see_every_department_and_are_refused_the_same(owner, reader, role):
    everything = await ask(owner, reader, ATTACKS["every department"][0], role, None)
    assert "Grocery Harvest Sale" in leaked(everything.answer.result.rows)   # the check does find what is there
    for attack, (sql, outcome) in ATTACKS.items():
        if outcome == REFUSED:
            traced = await ask(owner, reader, sql, role, None)
            assert traced.answer.status == pipeline.FAILED and traced.attempts[0].ran is None, attack


@pytest.mark.anyio
async def test_a_slow_query_is_stopped_by_the_time_limit(owner, reader):
    traced = await ask(owner, reader, "SELECT count(*) FROM generate_series(1, 1000000000) AS g", "staff", "Beauty",
                       budget=1.5)
    assert traced.answer.status == pipeline.FAILED and traced.attempts[0].error.startswith("QueryCanceled")


# ---- the code alone: guard.py's checks and filter, with row-level security letting every row through


async def code_alone(owner, sql: str, *, one_department: bool = True) -> list[tuple]:
    ran = guard.checked(sql, VIEWS, one_department=one_department)
    async with owner.connection() as conn, conn.transaction():
        await conn.execute("SET LOCAL app.department = 'Beauty'")
        await conn.execute("SET LOCAL app.all_departments = 'on'")
        return await (await conn.execute(ran)).fetchall()


@pytest.mark.anyio
async def test_with_row_level_security_letting_every_row_through_an_unfiltered_query_sees_both(owner):
    """What makes the next test mean something: here only the code's filter is between a query and Fresh Grocery."""
    assert leaked(await code_alone(owner, ATTACKS["every department"][0], one_department=False))


@pytest.mark.anyio
@pytest.mark.parametrize("attack", ATTACKS)
async def test_the_code_alone_keeps_the_department(owner, attack):
    sql, outcome = ATTACKS[attack]
    try:
        rows = await code_alone(owner, sql)
    except SqlRejected:
        assert outcome == REFUSED
        return
    except psycopg.errors.UndefinedColumn:
        assert outcome == FAILS
        return
    assert outcome == FILTERED and not leaked(rows)


# ---- the database alone: the query as written, as sql_reader in the asker's department


def database_alone(database, login, sql: str) -> list[tuple]:
    """Every result of the statement, the way psycopg sends a query without parameters: several statements at once."""
    with connect_as(database, login) as conn, conn.transaction():
        conn.execute("SET TRANSACTION READ ONLY")
        conn.execute("SET LOCAL app.department = 'Beauty'")
        cursor, rows = conn.execute(sql), []
        while True:
            if cursor.description:
                rows += cursor.fetchall()
            if not cursor.nextset():
                return rows


@pytest.mark.parametrize("attack", [name for name in ATTACKS if name != SECOND_STATEMENT])
def test_the_database_alone_keeps_the_department(database, reader_login, attack):
    try:
        rows = database_alone(database, reader_login, ATTACKS[attack][0])
    except (psycopg.errors.InsufficientPrivilege, psycopg.errors.UndefinedColumn):
        return   # refused: a table, set_config, a function reading a table, ip_address
    assert not leaked(rows)


def test_the_statistics_catalog_holds_values_it_shows_only_to_who_may_read_the_table(database, reader_login):
    """A known way around row-level security: the planner's statistics sample a column's values. pg_stats shows a
    column only to a role that may read it and isn't held by row-level security on it. The system catalogs' own
    statistics are there for everyone, as the catalogs are."""
    business = "SELECT DISTINCT tablename FROM pg_stats WHERE schemaname = 'public' ORDER BY 1"
    with db.connect(database) as conn:
        assert leaked(conn.execute(ATTACKS["the statistics catalog"][0]).fetchall())
        assert ("campaigns",) in conn.execute(business).fetchall()
    assert not leaked(database_alone(database, reader_login, ATTACKS["the statistics catalog"][0]))
    assert database_alone(database, reader_login, business) == []


def test_the_leak_check_finds_what_only_another_department_has_and_not_what_the_question_names(database):
    """evaluation.sql_leak's check, on the same data: names, amounts as written or with separators, a department's
    name only as a value."""
    with db.connect(database) as conn:
        c = sql_leak.canaries(conn)
    assert {"Grocery Harvest Sale", "Ana Ruiz", "Fresh Cherries"} <= c.names["Fresh Grocery"]
    assert "Grocery Harvest Sale" not in c.names["Beauty"]
    assert {"3000.50", "3,000.50"} <= c.amounts["Beauty"]   # Beauty Summer Sale's GMV, $1,000 or more
    question = "How did Grocery Harvest Sale do?"
    assert sql_leak.found(c, "Fresh Grocery", [("Fresh Cherries", "5")], "", question) == ["Fresh Cherries"]
    assert sql_leak.found(c, "Fresh Grocery", [], "Ana Ruiz hosted it.", question) == ["Ana Ruiz"]
    assert sql_leak.found(c, "Fresh Grocery", [], "Grocery Harvest Sale isn't yours to see.", question) == []
    assert sql_leak.found(c, "Fresh Grocery", [], "Fresh Grocery's data isn't available to you.", "") == []
    assert sql_leak.found(c, "Fresh Grocery", [("Fresh Grocery", "1")], "", "") == ["Fresh Grocery"]
    assert sql_leak.found(c, "Beauty", [("3000.50",)], "It made $13,000.50 in total.", "") == ["3000.50"]
    assert sql_leak.found(c, "Beauty", [], "It made $3,000.50.", "") == ["3,000.50"]


def test_a_value_two_departments_share_is_no_ones_canary():
    class Rows:
        def execute(self, sql):
            if "FROM departments" in sql and "JOIN" not in sql:
                return [("Beauty",), ("Fresh Grocery",)]
            if "product_name" in sql:
                return [("Beauty", "Serum"), ("Fresh Grocery", "Serum"), ("Beauty", "Lipstick")]
            if "unnest" in sql:
                return [("Beauty", Decimal("1500.00")), ("Fresh Grocery", Decimal("1500.00")),
                        ("Fresh Grocery", Decimal("2500.00")), ("Fresh Grocery", Decimal("999.99"))]
            return []

    c = sql_leak.canaries(Rows())
    assert c.names == {"Beauty": frozenset({"Lipstick"})}
    assert c.amounts == {"Fresh Grocery": frozenset({"2500.00", "2,500.00"})}   # under $1,000 is too common


def test_the_database_alone_runs_a_second_statement_so_the_code_refuses_it(database, reader_login):
    """sql_reader has to be able to set its department, so a second statement can set another. Two checks keep it to
    one statement: guard.py parses exactly one and regenerates the query from it, and run_select refuses a semicolon."""
    assert leaked(database_alone(database, reader_login, ATTACKS[SECOND_STATEMENT][0]))
    with pytest.raises(SqlRejected, match="exactly one statement"):
        guard.checked(ATTACKS[SECOND_STATEMENT][0], VIEWS, one_department=True)
