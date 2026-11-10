"""The Text-to-SQL groundwork against a throwaway database: the metric views, the data dictionary a model is shown,
running a generated query, the baseline agent with a fake model, and what its evaluation run scores."""
from datetime import date
from decimal import Decimal

import psycopg
import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage
from postgres_helpers import postgres_available, seed_two_departments, throwaway_database

from app import db
from app.config import settings
from app.sql_agent import schema
from app.sql_agent.execute import SqlRejected, run_select
from evaluation import sql_agent, sql_run
from evaluation.dataset import Asker, Question, split_for

pytestmark = pytest.mark.skipif(not postgres_available(), reason="needs the docker compose Postgres")

TODAY = date(2026, 10, 15)
GMV_SQL = "SELECT total_gmv FROM campaign_summary WHERE campaign_name = 'Beauty Summer Sale'"


@pytest.fixture(scope="module")
def database():
    with throwaway_database() as name:
        with db.connect(name) as conn:
            seed_two_departments(conn)
        yield name


@pytest.fixture
async def conn(database):
    """A plain connection, not read-only by itself, so a test shows what run_select adds. Set to see every department,
    as an executive does: the views apply row-level security to the tables' owner too (migration V11)."""
    conninfo = psycopg.conninfo.make_conninfo(host=settings.postgres_host, port=settings.postgres_port, dbname=database,
                                              user=settings.postgres_user, password=settings.postgres_password)
    async with await psycopg.AsyncConnection.connect(conninfo, autocommit=True) as opened:
        await opened.execute("SET app.all_departments = 'on'")
        yield opened


@pytest.fixture
async def pool(database):
    async with db.query_pool(database) as opened:
        await opened.wait()
        yield opened


async def fetch(conn, sql):
    return await (await conn.execute(sql)).fetchall()


# ---- the views


@pytest.mark.anyio
async def test_no_view_has_the_ip_address(conn):
    columns = await fetch(conn, "SELECT table_name FROM information_schema.columns "
                                "WHERE column_name = 'ip_address' AND table_name = ANY(ARRAY"
                                f"{list(schema.METRIC_VIEWS)})")
    assert columns == []


@pytest.mark.anyio
async def test_a_campaign_summary_adds_up_its_days_and_its_products_without_multiplying_them(conn):
    [row] = await fetch(conn, "SELECT department, host_name, days_reported, total_gmv, avg_conversion_rate, "
                              "products_featured, total_units_sold, total_profit, roi "
                              "FROM campaign_summary WHERE campaign_name = 'Beauty Summer Sale'")
    assert row == ("Beauty", "Tommy Walter", 2, Decimal("3000.50"), Decimal("0.15"), 3, 60, Decimal("120.00"),
                   Decimal("1.2"))


@pytest.mark.anyio
async def test_roi_is_null_until_the_actual_cost_is_in(conn):
    [row] = await fetch(conn, "SELECT roi, products_featured, total_gmv FROM campaign_summary "
                              "WHERE campaign_name = 'Beauty Fan Day'")
    assert row == (None, 0, Decimal("300.00"))


@pytest.mark.anyio
async def test_the_detail_views_carry_the_campaigns_name_and_department(conn):
    assert await fetch(conn, "SELECT department, campaign_name, product_name FROM campaign_product_sales "
                             "WHERE product_name = 'Fresh Cherries'") == [("Fresh Grocery", "Grocery Harvest Sale",
                                                                           "Fresh Cherries")]
    assert await fetch(conn, "SELECT department, customer_ref FROM campaign_customers ORDER BY 2") == [
        ("Beauty", "c-1"), ("Fresh Grocery", "c-2")]


# ---- what a model is shown


@pytest.mark.anyio
async def test_the_dictionary_is_shown_only_when_asked_for(conn):
    [metrics] = await schema.relations(conn, ["campaign_metrics"])
    assert "Gross merchandise value" in schema.describe([metrics], comments=True)
    plain = schema.describe([metrics], comments=False)
    assert "gmv numeric(14,2)" in plain and "--" not in plain


@pytest.mark.anyio
async def test_the_ip_address_is_never_shown(conn):
    [behavior] = await schema.relations(conn, ["campaign_customer_behavior"])
    assert "ip_address" not in schema.describe([behavior], comments=True)
    assert "region" in schema.describe([behavior], comments=False)


@pytest.mark.anyio
async def test_an_unknown_table_is_an_error_not_a_smaller_schema(conn):
    with pytest.raises(ValueError, match="campaign_kpis"):
        await schema.relations(conn, ["campaigns", "campaign_kpis"])


@pytest.mark.anyio
async def test_a_staff_member_is_shown_the_names_of_their_own_department_only(conn):
    values = await schema.known_values(conn, "staff", "Beauty")
    assert values["departments"] == ["Beauty"]
    assert values["campaign names"] == ["Beauty Fan Day", "Beauty Summer Sale"]   # latest first
    assert "Fresh Cherries" not in values["product names"] and values["regions"] == ["Midwest"]
    everything = await schema.known_values(conn, "executive", None)
    assert "Grocery Harvest Sale" in everything["campaign names"] and len(everything["departments"]) == 2


def test_a_role_without_its_department_or_an_unknown_role_fails_closed():
    with pytest.raises(ValueError):
        schema.visible_department("staff", None)
    with pytest.raises(ValueError):
        schema.visible_department("intern", "Beauty")


# ---- running a generated query


@pytest.mark.anyio
async def test_only_a_single_select_runs(conn):
    for sql in ("UPDATE campaigns SET name = 'x'", "SELECT 1; SELECT 2", "DELETE FROM campaigns"):
        with pytest.raises(SqlRejected):
            await run_select(conn, sql)


@pytest.mark.anyio
async def test_a_select_that_tries_to_write_fails_in_its_read_only_transaction(conn):
    with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
        await run_select(conn, "WITH gone AS (DELETE FROM campaigns RETURNING 1) SELECT count(*) FROM gone")
    assert await fetch(conn, "SELECT count(*) FROM campaigns") == [(3,)]


@pytest.mark.anyio
async def test_a_slow_query_is_stopped_at_its_time_limit(conn):
    with pytest.raises(psycopg.errors.QueryCanceled):
        await run_select(conn, "SELECT pg_sleep(2)", timeout_ms=100)


@pytest.mark.anyio
async def test_rows_past_the_limit_are_cut_and_marked(conn):
    result = await run_select(conn, "SELECT g FROM generate_series(1, 5) g", max_rows=3)
    assert result.rows == (("1",), ("2",), ("3",)) and result.truncated
    assert not (await run_select(conn, "SELECT 1", max_rows=3)).truncated


@pytest.mark.anyio
async def test_values_come_back_as_text_with_their_column_names(conn):
    result = await run_select(conn, "SELECT name, actual_operating_cost AS cost FROM campaigns "
                                    "WHERE name LIKE 'Beauty%' ORDER BY start_date;")
    assert result.columns == ("name", "cost")
    assert result.rows == (("Beauty Summer Sale", "100.00"), ("Beauty Fan Day", None))


# ---- the agent, with a fake model


class FakeModel(GenericFakeChatModel):
    """Replies with the messages it was given, one per call, and ignores the tools it is bound to."""

    def bind_tools(self, tools, **kwargs):
        return self


def asks_for(name: str, call: int = 0, **args) -> AIMessage:
    return AIMessage(content="", id=f"reply-{name}-{call}-{sorted(args.items())}",
                     tool_calls=[{"name": name, "args": args, "id": f"call-{name}-{call}"}])


@pytest.mark.anyio
async def test_the_agent_looks_runs_corrects_itself_and_is_scored_on_its_last_query(pool):
    model = FakeModel(messages=iter([
        asks_for("list_tables"),
        asks_for("describe_tables", table_names="campaign_metrics"),
        asks_for("run_query", 1, sql="SELECT gmv_total FROM campaign_metrics"),
        asks_for("run_query", 2, sql="SELECT sum(gmv) FROM campaign_metrics WHERE campaign_id = 1"),
        AIMessage(content="The Beauty Summer Sale brought in $3,000.50."),
    ]))
    run = await sql_agent.ask(model, pool, "How much did the summer sale bring in?", role="staff",
                              department="Beauty", today=TODAY)
    assert [bool(q.error) for q in run.queries] == [True, False]
    assert "gmv_total" in run.queries[0].error
    assert run.final.sql.endswith("campaign_id = 1") and run.final.result.rows == (("3000.50",),)
    assert run.reply == "The Beauty Summer Sale brought in $3,000.50."
    assert (run.model_calls, run.stopped) == (5, False)


@pytest.mark.anyio
async def test_the_agent_is_stopped_after_its_last_allowed_model_call(pool):
    model = FakeModel(messages=iter([asks_for("list_tables", call) for call in range(sql_agent.MAX_MODEL_CALLS + 5)]))
    run = await sql_agent.ask(model, pool, "How much?", role="executive", department=None, today=TODAY)
    assert run.stopped and run.reply is None and run.model_calls == sql_agent.MAX_MODEL_CALLS


@pytest.mark.anyio
async def test_the_agent_is_offered_the_tables_or_the_views_and_nothing_else(pool):
    run = sql_agent.AgentRun()
    tables = {t.name: t for t in sql_agent.make_tools(pool, run, role="staff", department="Beauty",
                                                      dictionary=False, views=False)}
    assert await tables["list_tables"].ainvoke({}) == ", ".join(schema.BASE_TABLES)
    refused = await tables["describe_tables"].ainvoke({"table_names": "campaign_summary, users"})
    assert refused.startswith("Error: no such table: campaign_summary, users")
    views = {t.name: t for t in sql_agent.make_tools(pool, run, role="staff", department="Beauty",
                                                     dictionary=False, views=True)}
    assert "VIEW campaign_summary" in await views["describe_tables"].ainvoke({"table_names": "campaign_summary"})


@pytest.mark.anyio
async def test_the_dictionary_and_the_askers_names_are_shown_only_when_asked_for(pool):
    run = sql_agent.AgentRun()
    plain, with_dictionary = (
        {t.name: t for t in sql_agent.make_tools(pool, run, role="staff", department="Beauty", dictionary=d,
                                                 views=False)}["describe_tables"]
        for d in (False, True))
    assert "Values in the data" not in await plain.ainvoke({"table_names": "campaigns"})
    shown = await with_dictionary.ainvoke({"table_names": "campaigns"})
    assert "NULL until the campaign is finished" in shown
    assert "campaign names: Beauty Fan Day, Beauty Summer Sale" in shown and "Grocery" not in shown


@pytest.mark.anyio
async def test_a_query_the_database_refuses_is_shown_to_the_model_as_an_error(pool):
    run = sql_agent.AgentRun()
    query = {t.name: t for t in sql_agent.make_tools(pool, run, role="executive", department=None,
                                                     dictionary=False, views=False)}["run_query"]
    assert (await query.ainvoke({"sql": "UPDATE campaigns SET name = 'x'"})).startswith("Error: SqlRejected")
    assert await query.ainvoke({"sql": "SELECT 1 AS first"}) == "first\n1"
    assert await query.ainvoke({"sql": GMV_SQL}) == "total_gmv\n3000.50"
    assert run.final.sql == GMV_SQL   # the last one that ran, not the first


def test_the_instructions_say_who_asks_what_they_may_see_and_what_day_it_is():
    staff = sql_agent.instructions("staff", "Beauty", TODAY)
    assert "a staff in the Beauty department" in staff and "keep to the Beauty department" in staff
    assert "Today is 2026-10-15" in staff
    assert "may see every department" in sql_agent.instructions("executive", None, TODAY)


# ---- the run: what is scored


def data_question(gold_result, *, role="staff", department="Beauty") -> Question:
    return Question(id="q-sql", question="How much did the Beauty Summer Sale bring in?",
                    asker=Asker(department=department, role=role), route="data", expected="answer",
                    gold_sql=GMV_SQL, gold_result=gold_result, answer_points=("its GMV",), tags=("direct",),
                    source="generated:test", group="g1", split=split_for("q-sql", "g1", "answer", "generated:test"))


def answering_with(sql: str) -> FakeModel:
    return FakeModel(messages=iter([asks_for("run_query", sql=sql), AIMessage(content="It was $3,000.50.")]))


@pytest.mark.anyio
async def test_a_run_is_scored_on_its_last_query_and_on_naming_the_department(pool):
    unfiltered = await sql_run.ask_one(answering_with("SELECT sum(gmv) FROM campaign_metrics WHERE campaign_id = 1"),
                                       pool, data_question((("Beauty Summer Sale", "3000.50"),)), {"Beauty": 1},
                                       dictionary=False, views=False)
    assert (unfiltered["status"], unfiltered["exact"], unfiltered["exact_every_value"]) == ("answered", True, False)
    assert unfiltered["names_department"] is False
    named = await sql_run.ask_one(answering_with(GMV_SQL + " AND department = 'Beauty'"), pool,
                                  data_question((("3000.50",),)), {"Beauty": 1}, dictionary=False, views=False)
    assert named["names_department"] is True and named["exact_every_value"]


@pytest.mark.anyio
async def test_an_executives_query_is_not_checked_for_a_department(pool):
    record = await sql_run.ask_one(answering_with(GMV_SQL), pool,
                                   data_question((("3000.50",),), role="executive", department=None), {},
                                   dictionary=False, views=False)
    assert record["exact"] and record["names_department"] is None


@pytest.mark.anyio
async def test_a_stopped_run_is_not_scored_even_when_a_query_was_right(pool):
    calls = [asks_for("run_query", 0, sql=GMV_SQL)]
    calls += [asks_for("list_tables", call) for call in range(1, sql_agent.MAX_MODEL_CALLS + 2)]
    record = await sql_run.ask_one(FakeModel(messages=iter(calls)), pool, data_question((("3000.50",),)),
                                   {"Beauty": 1}, dictionary=False, views=False)
    assert record["status"] == "stopped" and not record["exact"] and record["queries"][0]["rows"] == [["3000.50"]]


@pytest.mark.anyio
async def test_a_question_past_its_time_limit_keeps_the_queries_it_started(pool, monkeypatch):
    monkeypatch.setattr(sql_run, "QUESTION_TIMEOUT_SECONDS", 0.5)
    record = await sql_run.ask_one(answering_with("SELECT pg_sleep(3)"), pool, data_question((("3000.50",),)),
                                   {"Beauty": 1}, dictionary=False, views=False)
    assert record["status"] == "timeout" and not record["exact"]
    assert [q["sql"] for q in record["queries"]] == ["SELECT pg_sleep(3)"]


@pytest.mark.anyio
async def test_the_report_puts_the_number_questions_first_and_lists_the_wrong_ones(pool):
    right = await sql_run.ask_one(answering_with(GMV_SQL), pool, data_question((("3000.50",),)), {"Beauty": 1},
                                  dictionary=False, views=False)
    wrong = await sql_run.ask_one(answering_with("SELECT 1"), pool, data_question((("3000.50",),)), {"Beauty": 1},
                                  dictionary=False, views=False)
    report = sql_run.summarize([right, wrong | {"id": "q-wrong"}], "test", "dev", "zero-shot", "instructions", 1.0)
    assert "| **data: the number is the answer** | 2 | 50.0% |" in report
    assert "`q-wrong`" in report and "`q-sql`" not in report.split("## Wrong")[1]
    assert "don't name the asker's department: 2 of 2" in report
