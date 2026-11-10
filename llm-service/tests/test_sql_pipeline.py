"""The fixed Text-to-SQL pipeline: the checks every generated query passes, the department filter, the example
questions, and the pipeline itself with fake models against a throwaway database."""
import re
from datetime import date
from pathlib import Path

import psycopg
import pytest
from postgres_helpers import login_in, postgres_available, seed_two_departments, throwaway_database
from sqlglot import exp

from app import db
from app.config import settings
from app.rag.answering import InvalidAnswer
from app.rag.query_models import ModelAnswer
from app.sql_agent import examples, guard, pipeline, schema
from app.sql_agent.execute import SqlRejected, run_select
from app.sql_agent.pipeline import SqlOptions, SqlService
from app.time_budget import TimeBudget
from evaluation import sql_run
from evaluation.dataset import Asker, Question, load, split_for
from scripts.generate_mock_data import DEPARTMENTS, PRODUCTS_BY_DEPT

EVAL_DIR = Path(__file__).resolve().parents[2] / "eval"
TODAY = date(2026, 10, 15)
VIEWS = guard.METRIC_VIEWS
needs_postgres = pytest.mark.skipif(not postgres_available(), reason="needs the docker compose Postgres")


def rejected(sql: str, relations=VIEWS) -> str:
    with pytest.raises(SqlRejected) as caught:
        guard.checked(sql, relations, one_department=True)
    return str(caught.value)


# ---- the checks


def test_only_a_single_select_is_allowed():
    assert "exactly one statement" in rejected("SELECT 1; DELETE FROM campaigns")
    for sql in ("UPDATE campaigns SET name = 'x'", "COPY campaigns TO '/tmp/x'", "TABLE campaign_summary",
                "VALUES (1)", "SET app.department = 'Fresh Grocery'"):
        assert "only a SELECT" in rejected(sql)
    assert "doesn't parse" in rejected("SELEC campaign_name FROM campaign_summary")


def test_a_query_that_doesnt_parse_is_told_where_without_terminal_codes():
    reason = rejected("Query:\nSELECT campaign_name FROM campaign_summary")
    assert reason == "the query doesn't parse: Invalid expression / Unexpected token at line 1, column 6"


def test_only_the_offered_views_can_be_read():
    for sql, name in (("SELECT * FROM campaigns", "campaigns"), ("SELECT * FROM pg_authid", "pg_authid"),
                      ("SELECT * FROM document_catalog", "document_catalog"),
                      ("SELECT (SELECT relname FROM pg_class LIMIT 1)", "pg_class"),
                      ('SELECT * FROM "Campaign_Summary"', "Campaign_Summary")):
        assert f"no such view: {name}" in rejected(sql)
    for sql in ("SELECT * FROM pg_catalog.pg_authid", "SELECT * FROM public.campaign_summary",
                "SELECT * FROM information_schema.tables"):
        assert "without a schema" in rejected(sql)


def test_a_with_query_cant_make_a_real_table_look_allowed():
    # Inside a WITH query only the ones before it are visible: this pg_authid is the real one
    assert "no such view: pg_authid" in rejected(
        "WITH x AS (SELECT * FROM pg_authid), pg_authid AS (SELECT 1) SELECT * FROM x")
    # A WITH query in a subquery is visible there only
    assert "no such view: campaigns" in rejected(
        "SELECT * FROM campaigns, (WITH campaigns AS (SELECT 1) SELECT * FROM campaigns) c")
    assert "name of a view" in rejected("WITH campaign_summary AS (SELECT 1) SELECT * FROM campaign_summary")
    assert "RECURSIVE" in rejected("WITH RECURSIVE r AS (SELECT 1) SELECT * FROM r")
    guard.checked("WITH x AS (SELECT * FROM campaign_summary), y AS (SELECT * FROM x) SELECT * FROM y", VIEWS,
                  one_department=False)
    guard.checked("SELECT campaign_name FROM Campaign_Summary", VIEWS, one_department=False)   # unquoted: folded


def test_functions_off_the_allow_list_are_refused_wherever_they_are():
    for sql, name in (("SELECT set_config('app.department', 'Fresh Grocery', true), * FROM campaign_summary",
                       "set_config"),
                      ("SELECT * FROM campaign_summary WHERE department = current_setting('app.department')",
                       "current_setting"),
                      ("SELECT pg_sleep(10)", "pg_sleep"),
                      ("SELECT * FROM dblink('host=x', 'select 1') AS t(a int)", "dblink"),
                      ("SELECT lo_import('/etc/passwd')", "lo_import"),
                      ("SELECT * FROM ROWS FROM (pg_ls_dir('.')) t", "pg_ls_dir"),
                      ("WITH x AS (SELECT query_to_xml('select 1', true, true, '')) SELECT * FROM x", "query_to_xml"),
                      ("SELECT version()", "VERSION()"),
                      ("SELECT repeat('x', 1000000000)", "REPEAT")):
        assert "function not allowed" in rejected(sql) and name in rejected(sql)


def test_casts_to_catalog_types_are_refused():
    assert "type not allowed: REGCLASS" in rejected("SELECT 'pg_authid'::regclass")
    assert "type not allowed: OID" in rejected("SELECT CAST(1 AS oid)")


def test_writing_locking_select_into_and_parameters_are_refused():
    assert "DELETE" in rejected("WITH gone AS (DELETE FROM campaigns RETURNING 1) SELECT count(*) FROM gone")
    assert "INTO" in rejected("SELECT * INTO copied FROM campaign_summary")
    assert "LOCK" in rejected("SELECT * FROM campaign_summary FOR UPDATE")
    assert "PARAMETER" in rejected("SELECT * FROM campaign_summary WHERE campaign_id = $1")
    assert "PLACEHOLDER" in rejected("SELECT * FROM campaign_summary WHERE campaign_id = %s")


def test_every_allowed_function_and_type_exists_in_sqlglot():
    """A name sqlglot doesn't know would allow nothing, silently; an upgrade that renames a class shows up here."""
    assert all(issubclass(getattr(exp, name), exp.Func) for name in guard.ALLOWED_FUNCTIONS)


def test_the_gold_queries_of_dev_and_the_examples_pass():
    """What a right answer needs is allowed: dev's gold queries read the tables, the examples the views."""
    gold = [q.gold_sql for q in load(EVAL_DIR / "dev.jsonl") if q.gold_sql]
    for sql in gold:
        guard.checked(sql, sql_run.TABLES_IN_ONE_DEPARTMENT, one_department=True)
    for example in examples.read_file():
        guard.checked(example.sql, VIEWS, one_department=True)
    assert len(gold) > 30


def test_what_runs_is_regenerated_from_the_checked_tree_without_comments():
    ran = guard.checked("SELECT campaign_name -- ; DROP TABLE campaigns\nFROM campaign_summary", VIEWS,
                        one_department=False)
    assert ran == "SELECT campaign_name FROM campaign_summary LIMIT 201"


def limited(sql: str) -> str:
    return guard.checked(sql, VIEWS, one_department=False)


def test_a_query_returns_one_row_more_than_is_kept_at_most_and_a_smaller_limit_stays():
    base = "SELECT campaign_name FROM campaign_summary"
    assert guard.ROW_LIMIT == 201
    assert limited(base) == f"{base} LIMIT 201"
    assert limited(f"{base} LIMIT 5") == f"{base} LIMIT 5"
    assert limited(f"{base} LIMIT 1000 OFFSET 3") == f"{base} LIMIT 201 OFFSET 3"
    assert limited(f"{base} LIMIT ALL") == f"{base} LIMIT 201"
    assert limited(f"{base} FETCH FIRST 300 ROWS ONLY") == f"{base} FETCH FIRST 201 ROWS ONLY"
    # Not a number: capped, so it still means what it said
    assert limited(f"{base} LIMIT (SELECT count(*) FROM host_profiles)") == (
        f"{base} LIMIT LEAST((SELECT COUNT(*) FROM host_profiles), 201)")
    assert "WITH TIES is not allowed" in rejected(f"{base} ORDER BY 1 FETCH FIRST 3 ROWS WITH TIES")


def test_only_the_outer_query_is_limited():
    union = limited("SELECT campaign_name FROM campaign_summary UNION SELECT host_name FROM host_profiles")
    assert union.endswith("SELECT host_name FROM host_profiles LIMIT 201") and union.count("LIMIT") == 1
    inner = "WITH x AS (SELECT campaign_name FROM campaign_summary LIMIT 999) SELECT * FROM x"
    assert limited(inner) == f"{inner} LIMIT 201"


def test_every_view_read_becomes_its_one_department_subquery_under_the_same_name():
    ran = guard.checked("""
        WITH top AS (SELECT campaign_id FROM campaign_product_sales WHERE profit > 0)
        SELECT s.campaign_name, d.gmv FROM campaign_summary s
        JOIN campaign_daily_metrics AS d ON d.campaign_id = s.campaign_id
        WHERE s.campaign_id IN (SELECT campaign_id FROM top)
        UNION ALL SELECT host_name, NULL FROM host_profiles""", VIEWS, one_department=True)
    filtered = re.findall(r"\(SELECT \* FROM (\w+) WHERE department = CURRENT_SETTING\('app.department'\)\) AS (\w+)",
                          ran)
    assert sorted(filtered) == [("campaign_daily_metrics", "d"), ("campaign_product_sales", "campaign_product_sales"),
                                ("campaign_summary", "s"), ("host_profiles", "host_profiles")]
    unfiltered = guard.checked("SELECT campaign_name FROM campaign_summary", VIEWS, one_department=False)
    assert "current_setting" not in unfiltered.lower()


def test_a_reply_in_a_code_fence_is_unwrapped():
    assert pipeline.written_query("```sql\nSELECT 1\n```") == "SELECT 1"
    assert pipeline.written_query("  SELECT 1  ") == "SELECT 1"


# ---- the examples


def test_the_examples_name_no_real_department_campaign_or_product():
    """Everyone is shown them: a real name would tell a staff member about another department's campaigns."""
    names = [*DEPARTMENTS, *(label for label, _ in DEPARTMENTS.values()),
             *(product for products in PRODUCTS_BY_DEPT.values() for product in products)]
    for example in examples.read_file():
        text = f"{example.question}\n{example.sql}"
        assert not [n for n in names if re.search(rf"\b{re.escape(n)}\b", text, re.IGNORECASE)], example.question


def test_no_example_repeats_an_evaluation_question():
    def words(text: str) -> str:
        return " ".join(re.findall(r"[a-z0-9]+", text.lower()))
    asked = {words(q.question) for split in ("dev", "test") for q in load(EVAL_DIR / f"{split}.jsonl")}
    assert not [e.question for e in examples.read_file() if words(e.question) in asked]


# ---- against a throwaway database


@pytest.fixture(scope="module")
def database():
    if not postgres_available():
        pytest.skip("needs the docker compose Postgres")
    with throwaway_database() as name:
        with db.connect(name) as conn:
            seed_two_departments(conn)
            # Three examples: one for everyone, one for each department. Vectors one-hot, so the closest is known
            examples.replace_all(conn, [examples.Example("q all", "SELECT 1"),
                                        examples.Example("q beauty", "SELECT 2", "Beauty"),
                                        examples.Example("q grocery", "SELECT 3", "Fresh Grocery")],
                                 [unit(0), unit(1), unit(2)], FakeEmbedder.model)
        yield name


@pytest.fixture
async def pool(database):
    """As the owner: the tests of the code's department filter alone run here, without row-level security."""
    async with db.query_pool(database) as opened:
        await opened.wait()
        yield opened


@pytest.fixture(scope="module")
def reader_login(database):
    with login_in("sql_reader") as login:
        yield login


@pytest.fixture
async def pools(pool, database, reader_login):
    """The pipeline's two: pool for its own reads, and for the model's queries a login in sql_reader, under row-level
    security as well as the code's filter."""
    user, password = reader_login
    async with db.sql_pool(database, user=user, password=password) as reader:
        await reader.wait()
        yield pool, reader


def unit(i: int) -> list[float]:
    return [1.0 if j == i else 0.0 for j in range(1536)]


async def run_as(pool, sql: str, department: str | None, relations=VIEWS):
    async with pool.connection() as conn:
        return await run_select(conn, guard.checked(sql, relations, one_department=department is not None),
                                department=department, every_department=department is None)


@needs_postgres
@pytest.mark.anyio
async def test_a_staff_query_reads_only_its_department_whatever_it_asks_for(pool):
    everything = "SELECT department, count(*) FROM campaign_summary GROUP BY department ORDER BY department"
    assert (await run_as(pool, everything, "Beauty")).rows == (("Beauty", "2"),)
    other = "SELECT campaign_name FROM campaign_summary WHERE department = 'Fresh Grocery'"
    assert (await run_as(pool, other, "Beauty")).rows == ()
    joined = ("SELECT s.campaign_name, c.customer_ref FROM campaign_summary s "
              "JOIN campaign_customers c ON c.campaign_id = s.campaign_id")
    assert (await run_as(pool, joined, "Fresh Grocery")).rows == (("Grocery Harvest Sale", "c-2"),)
    assert len((await run_as(pool, everything, None)).rows) == 2   # an executive's isn't filtered


@needs_postgres
@pytest.mark.anyio
async def test_the_department_holds_for_its_transaction_only_and_a_filter_without_it_fails_closed(pool, database):
    filtered = guard.checked("SELECT campaign_name FROM campaign_summary", VIEWS, one_department=True)
    async with pool.connection() as conn:
        result = await run_select(conn, "SELECT current_setting('app.department')", department="Beauty")
        assert result.rows == (("Beauty",),)
        # Once set in a session, the setting stays defined there, empty after its transaction: no department's rows
        assert (await run_select(conn, "SELECT current_setting('app.department')")).rows == (("",),)
        assert (await run_select(conn, filtered)).rows == ()
    # Never set in the session: an error. Either way never every department's rows
    conninfo = psycopg.conninfo.make_conninfo(host=settings.postgres_host, port=settings.postgres_port,
                                              dbname=database, user=settings.postgres_user,
                                              password=settings.postgres_password)
    async with await psycopg.AsyncConnection.connect(conninfo, autocommit=True) as fresh:
        with pytest.raises(psycopg.errors.UndefinedObject):
            await run_select(fresh, filtered)


@needs_postgres
@pytest.mark.anyio
async def test_the_tables_for_the_evaluation_are_filtered_the_same_way_and_hide_the_ip_address(pool):
    tables = sql_run.TABLES_IN_ONE_DEPARTMENT
    for name in tables:
        await run_as(pool, f"SELECT count(*) FROM {name}", "Beauty", tables)
    assert (await run_as(pool, "SELECT name FROM campaigns ORDER BY name", "Fresh Grocery", tables)).rows == (
        ("Grocery Harvest Sale",),)
    assert (await run_as(pool, "SELECT sum(units_sold) FROM campaign_products", "Beauty", tables)).rows == (("60",),)
    with pytest.raises(psycopg.errors.UndefinedColumn):
        await run_as(pool, "SELECT ip_address FROM campaign_customer_behavior", "Beauty", tables)


@needs_postgres
@pytest.mark.anyio
async def test_a_huge_result_stops_in_the_database_and_still_reads_as_cut_off(pool):
    ran = guard.checked("SELECT g FROM generate_series(1, 2000000) AS g", VIEWS, one_department=False)
    async with pool.connection() as conn:
        result = await run_select(conn, ran)
    assert ran.endswith("LIMIT 201") and len(result.rows) == 200 and result.truncated


@needs_postgres
@pytest.mark.anyio
async def test_every_example_runs_on_the_views(pool):
    for example in examples.read_file():
        await run_as(pool, example.sql, "Beauty")


def test_loading_the_examples_replaces_the_ones_there(database):
    with db.connect(database) as conn, conn.transaction(force_rollback=True):
        examples.replace_all(conn, [examples.Example("q new", "SELECT 4")], [unit(3)], FakeEmbedder.model)
        assert conn.execute("SELECT question FROM sql_examples").fetchall() == [("q new",)]


@needs_postgres
@pytest.mark.anyio
async def test_the_closest_examples_are_the_askers_departments_and_everyones(pool):
    async with pool.connection() as conn:
        closest = await examples.closest(conn, unit(2), ["Beauty", "ALL"], FakeEmbedder.model, 3)
        assert [e.question for e in closest] == ["q all", "q beauty"]   # never Fresh Grocery's
        assert [e.question for e in await examples.closest(conn, unit(2), None, FakeEmbedder.model, 1)] == [
            "q grocery"]
        assert await examples.closest(conn, unit(2), None, "another-model", 3) == []


# ---- the pipeline, with fake models


class FakeEmbedder:
    model = "fake-embedding"

    def __init__(self, vector: list[float]):
        self.vector = vector
        self.calls = 0

    async def embed_question(self, question: str, budget: TimeBudget) -> list[float]:
        self.calls += 1
        return self.vector


class FakeModel:
    """Replies with the texts it was given, one per call, and keeps what it was asked."""

    def __init__(self, *replies: str | ModelAnswer):
        self.replies = list(replies)
        self.prompts: list[tuple[str, str]] = []

    async def answer(self, instructions: str, prompt: str, budget: TimeBudget) -> ModelAnswer:
        self.prompts.append((instructions, prompt))
        reply = self.replies.pop(0)
        return reply if isinstance(reply, ModelAnswer) else ModelAnswer(reply, True, 100, 10)


GMV = "SELECT campaign_name, total_gmv FROM campaign_summary WHERE campaign_name = 'Beauty Summer Sale'"


def service(pools, writer, explainer, *, examples_shown=0, retries=2, embedder=None) -> SqlService:
    pool, reader = pools
    return SqlService(embedder or FakeEmbedder(unit(1)), writer, explainer, pool, reader,
                      SqlOptions(examples=examples_shown, retries=retries))


async def ask(sql_service: SqlService, question="How much did the summer sale bring in?", role="staff",
              department="Beauty", budget=10.0):
    return await sql_service.trace(question, role=role, department=department, today=TODAY,
                                   budget=TimeBudget(budget))


@needs_postgres
@pytest.mark.anyio
async def test_the_pipeline_writes_a_query_runs_it_in_the_askers_department_and_explains_it(pools):
    writer, explainer = FakeModel(GMV), FakeModel("The summer sale brought in $3,000.50.")
    traced = await ask(service(pools, writer, explainer, examples_shown=3))
    answer = traced.answer
    assert (answer.status, answer.text, answer.department) == ("answered", "The summer sale brought in $3,000.50.",
                                                               "Beauty")
    assert answer.result.rows == (("Beauty Summer Sale", "3000.50"),) and "CURRENT_SETTING" in answer.sql
    rules, prompt = writer.prompts[0]
    assert "a staff in the Beauty department" in rules and "Today is 2026-10-15" in rules
    assert "already holds only Beauty's rows, so don't filter by department" in rules
    assert "VIEW campaign_summary" in prompt and "ROI" in prompt            # the views with their dictionary
    assert "campaign names: Beauty Fan Day, Beauty Summer Sale" in prompt and "Grocery" not in prompt.split(
        "Examples")[0]
    assert "Example question: q beauty\nExample query:\nSELECT 2" in prompt and "q grocery" not in prompt
    assert prompt.endswith("Write the query for this question:\nHow much did the summer sale bring in?")
    explained = explainer.prompts[0][1]
    assert "Beauty Summer Sale | 3000.50" in explained and "only the Beauty department" in explainer.prompts[0][0]
    assert [e.question for e in traced.examples] == ["q beauty", "q all"] and len(traced.attempts) == 1


@needs_postgres
@pytest.mark.anyio
async def test_a_failed_query_is_shown_to_the_model_and_written_again(pools):
    writer = FakeModel("SELECT gmv_total FROM campaign_summary",
                       "SELECT set_config('app.department', 'Fresh Grocery', true)",
                       GMV)
    traced = await ask(service(pools, writer, FakeModel("$3,000.50.")))
    assert traced.answer.status == "answered" and traced.answer.result.rows[0][1] == "3000.50"
    assert [a.error.split(":")[0] if a.error else None for a in traced.attempts] == [
        "UndefinedColumn", "SqlRejected", None]
    second, third = writer.prompts[1][1], writer.prompts[2][1]
    assert "Query:\nSELECT gmv_total FROM campaign_summary\nError: UndefinedColumn" in second
    assert "function not allowed: set_config" in third and "gmv_total" in third   # every failure so far
    assert third.endswith("Write the corrected query for this question:\nHow much did the summer sale bring in?")
    assert traced.attempts[1].ran is None   # rejected before anything ran


@needs_postgres
@pytest.mark.anyio
async def test_it_gives_up_after_its_retries_without_explaining(pools):
    writer, explainer = FakeModel("SELECT nope FROM campaign_summary", "SELECT nope FROM campaign_summary"), FakeModel()
    traced = await ask(service(pools, writer, explainer, retries=1))
    assert (traced.answer.status, traced.answer.text) == ("failed", pipeline.FAILED_TEXT)
    assert len(traced.attempts) == 2 and explainer.prompts == []


@needs_postgres
@pytest.mark.anyio
async def test_a_question_the_data_cant_answer_is_refused_without_running_anything(pools):
    traced = await ask(service(pools, FakeModel("NOT_IN_DATA"), FakeModel()), question="Who will win the election?")
    assert (traced.answer.status, traced.answer.text, traced.attempts) == ("refused", pipeline.REFUSAL_TEXT, ())


@needs_postgres
@pytest.mark.anyio
async def test_another_departments_data_gets_the_fixed_sentence_without_a_model(pools):
    """The same sentence whether the other department has such data or not, and no model to word it differently."""
    explainer = FakeModel()
    other = "SELECT total_gmv FROM campaign_summary WHERE campaign_name = 'Grocery Harvest Sale'"
    traced = await ask(service(pools, FakeModel(other), explainer), question="What did Grocery Harvest Sale make?")
    assert (traced.answer.status, traced.answer.text, traced.answer.result.rows) == (
        "answered", pipeline.NO_ROWS_TEXT, ())
    assert explainer.prompts == []


@needs_postgres
@pytest.mark.anyio
async def test_an_explanation_cut_off_is_an_error_not_an_answer(pools):
    traced = await ask(service(pools, FakeModel(GMV), FakeModel(ModelAnswer("The summer sale", False, 100, 800))))
    assert traced.answer is None and isinstance(traced.error, InvalidAnswer)
    with pytest.raises(InvalidAnswer):
        await service(pools, FakeModel(GMV), FakeModel(ModelAnswer("", True, 1, 0))).answer(
            "How much?", user_id=1, role="staff", department="Beauty", budget=TimeBudget(10))


@needs_postgres
@pytest.mark.anyio
async def test_no_query_runs_once_the_budget_is_spent(pools):
    class SlowWriter(FakeModel):
        async def answer(self, instructions, prompt, budget):
            now[0] += 20   # the model call used up the 10 s
            return await super().answer(instructions, prompt, budget)

    now = [0.0]
    sql_service = service(pools, SlowWriter(GMV), FakeModel())
    with pytest.raises(TimeoutError, match="before the query could run"):
        await sql_service.trace("How much?", role="staff", department="Beauty", today=TODAY,
                                budget=TimeBudget(10, clock=lambda: now[0]))


@needs_postgres
@pytest.mark.anyio
async def test_without_examples_nothing_is_embedded_and_an_executive_sees_every_department(pools):
    embedder = FakeEmbedder(unit(0))
    writer = FakeModel("SELECT department, count(*) FROM campaign_summary GROUP BY 1 ORDER BY 1")
    traced = await ask(service(pools, writer, FakeModel("Two departments."), embedder=embedder),
                       role="executive", department=None)
    assert traced.answer.result.rows == (("Beauty", "2"), ("Fresh Grocery", "1")) and traced.answer.department is None
    assert embedder.calls == 0 and "Examples" not in writer.prompts[0][1]
    assert "may see every department" in writer.prompts[0][0]


@needs_postgres
@pytest.mark.anyio
async def test_the_models_query_runs_as_the_reader_and_the_pipelines_own_reads_as_the_owner(pools):
    """Offered the tables, as an evaluation run can be: their descriptions and names are read on the owner's pool, but
    the model's query runs on the reader's, which may read no table."""
    pool, reader = pools
    writer = FakeModel("SELECT count(*) FROM campaigns")
    sql_service = SqlService(None, writer, FakeModel(), pool, reader,
                             SqlOptions(examples=0, retries=0, relations=sql_run.TABLES_IN_ONE_DEPARTMENT))
    traced = await ask(sql_service)
    assert "TABLE campaigns" in writer.prompts[0][1] and "campaign names: Beauty Fan Day" in writer.prompts[0][1]
    assert traced.answer.status == "failed" and traced.attempts[0].error.startswith("InsufficientPrivilege")


def test_examples_without_an_embedder_are_refused_at_once():
    with pytest.raises(ValueError):
        SqlService(None, FakeModel(), FakeModel(), None, None, SqlOptions(examples=3, retries=2))


def test_the_query_path_runs_the_settings():
    options = SqlOptions.from_settings()
    assert (options.examples, options.retries, options.relations, options.dictionary) == (
        settings.sql_examples, settings.sql_retries, guard.METRIC_VIEWS, True)


# ---- the evaluation run


def data_question(gold_result, *, role="staff", department="Beauty") -> Question:
    return Question(id="q-sql", question="How much did the Beauty Summer Sale bring in?",
                    asker=Asker(department=department, role=role), route="data", expected="answer",
                    gold_sql=GMV, gold_result=gold_result, answer_points=("its GMV",), tags=("direct",),
                    source="generated:test", group="g1", split=split_for("q-sql", "g1", "answer", "generated:test"))


@needs_postgres
@pytest.mark.anyio
async def test_a_pipeline_run_is_scored_on_the_query_that_ran_and_reports_its_corrections(pools):
    corrected = await sql_run.ask_pipeline(
        service(pools, FakeModel("SELECT nope FROM campaign_summary", GMV), FakeModel("$3,000.50.")),
        data_question((("3000.50",),)))
    assert (corrected["status"], corrected["exact"], corrected["department_filtered"]) == ("answered", True, True)
    assert [q["error"] is None for q in corrected["queries"]] == [False, True]
    assert corrected["queries"][1]["rows"] == [["Beauty Summer Sale", "3000.50"]] and corrected["model_calls"] == 3
    failed = await sql_run.ask_pipeline(service(pools, FakeModel("SELECT 1 FROM nowhere"), FakeModel(), retries=0),
                                        data_question((("3000.50",),)))
    assert (failed["status"], failed["exact"], failed["final_sql"]) == ("failed", False, None)
    report = sql_run.summarize([corrected, failed | {"id": "q-failed"}], "test", "dev", "Fixed pipeline.", "rules",
                               1.0)
    assert "ran with the department filter the code adds: 1 of 1" in report
    assert "Answered on the first query: 0; after correcting: 1 after 1 retry" in report
    assert "`q-failed`: SqlRejected: no such view: nowhere" in report
