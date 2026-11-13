"""The top of the query path, with fake paths, router and models: what starts before the route is known, what the route
cancels, which answer reaches the asker, and that nothing a question started outlives it. No network, no database."""
import asyncio
import gc
from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest

from app.query_flow import NOT_IN_SOURCES, REFUSAL_TEXT, QueryFlow, compose_instructions
from app.rag.answering import ANSWERED, REFUSED, Citation, InvalidAnswer, QueryAnswer, QueryService, QueryTrace
from app.rag.answering import REFUSAL_TEXT as DOCUMENTS_REFUSAL
from app.rag.query_models import ModelAnswer, ModelBusy
from app.rag.retrieval import RetrievedChunk
from app.router.routing import BOTH, PATHS, Routing
from app.sql_agent import pipeline
from app.sql_agent.execute import QueryResult
from app.sql_agent.pipeline import FAILED, Attempt, SqlAnswer, SqlOptions, SqlService, SqlTrace
from app.time_budget import TimeBudget

TODAY = date(2026, 10, 15)
CHUNKS = [RetrievedChunk(chunk_id=i, doc_id=10 + i, doc_type="campaign_plan", title=f"Plan {i}", department="Beauty",
                         heading_path=("Plan", "Targets"), content=f"Target {i}: 10% conversion.", score=0.6)
          for i in (1, 2)]
SQL = "SELECT campaign_name, avg_conversion_rate FROM campaign_summary"
ROWS = QueryResult(("campaign_name", "avg_conversion_rate"), (("Beauty Summer Sale", "0.087"),), truncated=False)
NO_ROWS = QueryResult(("campaign_name",), (), truncated=False)


def documents_trace(status=ANSWERED, excerpts=CHUNKS, error=None) -> QueryTrace:
    result = (QueryAnswer(ANSWERED, "Aim for 10% [1].", [Citation(1, CHUNKS[0])], ["Beauty", "ALL"])
              if status == ANSWERED else QueryAnswer(REFUSED, DOCUMENTS_REFUSAL, [], ["Beauty", "ALL"]))
    return QueryTrace(None if error else result, error, False, list(excerpts), False, 0.6 if excerpts else 0.1,
                      0.0, 1.0, 0.0, 1.0, 100, 20, list(excerpts))


def data_trace(status=ANSWERED, rows=ROWS) -> SqlTrace:
    answer = {ANSWERED: SqlAnswer(ANSWERED, "", SQL, rows, "Beauty"),
              REFUSED: SqlAnswer(REFUSED, pipeline.REFUSAL_TEXT, None, None, "Beauty"),
              FAILED: SqlAnswer(FAILED, pipeline.FAILED_TEXT, None, None, "Beauty")}[status]
    attempts = () if status == REFUSED else (Attempt(SQL, SQL, None, 1.0, 1.0),)
    return SqlTrace(answer, None, (), attempts, 0.0, 1.0, 0.0, (300, 30), (0, 0))


class Recorded:
    """A step that takes some time, and remembers whether it started, finished or was cancelled."""

    def __init__(self, delay: float, value=None, error: Exception | None = None):
        self.delay, self.value, self.error = delay, value, error
        self.started = self.finished = self.cancelled = False
        self.cancelled_at = self.finished_at = None
        self.calls = []

    async def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        self.started = True
        try:
            await asyncio.sleep(self.delay)
        except asyncio.CancelledError:
            self.cancelled, self.cancelled_at = True, asyncio.get_running_loop().time()
            raise
        if self.error:
            # A new one each time: the step keeping the raised one would keep its traceback, and with it the failed task,
            # alive past the test, and asyncio reports an error nobody retrieved only when the task is collected
            raise type(self.error)(*self.error.args)
        self.finished, self.finished_at = True, asyncio.get_running_loop().time()
        return self.value


class FakeEmbedder:
    model = "fake-embedding"

    def __init__(self, delay=0.0, error=None):
        self.step = Recorded(delay, [0.1, 0.2], error)

    async def embed_question(self, question, budget):
        return await self.step(question)


class FakeRouter:
    def __init__(self, route="documents", delay=0.05, paths=None):
        self.step = Recorded(delay, Routing(route, 0.99, "Because.", paths or PATHS[route], "model", 300, 25))

    async def route(self, question, budget):
        return await self.step(question)


class FakeDocuments:
    def __init__(self, traced=None, delay=0.02, error=None):
        self.step = Recorded(delay, traced or documents_trace(), error)

    async def trace(self, question, *, role, department, budget, candidates=0, vector=None):
        return await self.step(question, role=role, department=department, vector=vector)


class FakeModel:
    def __init__(self, *replies):
        self.replies = list(replies)
        self.prompts = []

    async def answer(self, instructions, prompt, budget):
        self.prompts.append((instructions, prompt))
        reply = self.replies.pop(0)
        return reply if isinstance(reply, ModelAnswer) else ModelAnswer(reply, True, 200, 40)


class FakeData(SqlService):
    """The real explanation step, after a fake query step."""

    def __init__(self, traced=None, delay=0.02, error=None, explanation="It converted 8.7%."):
        self.explainer = FakeModel(explanation)
        super().__init__(None, FakeModel(), self.explainer, None, None, SqlOptions(examples=0, retries=2))
        self.step = Recorded(delay, traced or data_trace(), error)

    async def query(self, question, *, role, department, today, budget, vector=None):
        return await self.step(question, role=role, department=department, today=today, vector=vector)


def build(route="documents", *, documents=None, data=None, router=None, composer=None, embedder=None):
    parts = {"embedder": embedder or FakeEmbedder(), "router": router or FakeRouter(route),
             "documents": documents or FakeDocuments(), "data": data or FakeData(),
             "composer": composer or FakeModel("It converted 8.7%, below the 10% target [1].")}
    return QueryFlow(parts["embedder"], parts["router"], parts["documents"], parts["data"], parts["composer"]), parts


async def trace(flow, role="staff", department="Beauty", question="How did the summer sale convert against its plan?"):
    return await flow.trace(question, role=role, department=department, today=TODAY, budget=TimeBudget(10))


# ---- what starts, and what the route stops


@pytest.mark.anyio
async def test_both_paths_start_with_the_embedding_and_the_route_cancels_the_data_for_a_documents_question():
    flow, parts = build("documents", router=FakeRouter("documents", delay=0.1), data=FakeData(delay=5))
    traced = await trace(flow)
    assert parts["data"].step.started and parts["data"].step.cancelled
    assert parts["documents"].step.calls[0][1]["vector"] == [0.1, 0.2]   # the one embedding, shared
    assert parts["data"].step.calls[0][1]["vector"] == [0.1, 0.2] and parts["embedder"].step.calls == [(
        ("How did the summer sale convert against its plan?",), {})]
    assert traced.cancelled == ("data",) and traced.data is None and traced.written_by == "documents"
    assert (traced.answer.status, traced.answer.text, traced.answer.paths) == (ANSWERED, "Aim for 10% [1].",
                                                                               ("documents",))
    assert traced.answer.searched_departments == ["Beauty", "ALL"] and traced.answer.result is None


@pytest.mark.anyio
async def test_the_path_ruled_out_stops_when_the_route_comes_not_when_the_answer_is_done():
    flow, parts = build("documents", router=FakeRouter("documents", delay=0.05), documents=FakeDocuments(delay=0.3),
                        data=FakeData(delay=5))
    await trace(flow)
    assert parts["data"].step.cancelled_at < parts["documents"].step.finished_at


@pytest.mark.anyio
async def test_the_documents_answer_is_written_while_the_router_decides():
    flow, _ = build("documents", router=FakeRouter("documents", delay=0.3), documents=FakeDocuments(delay=0.25))
    traced = await trace(flow)
    assert traced.total_ms < 400   # not 0.3 s and then 0.25 s


@pytest.mark.anyio
async def test_a_data_question_cancels_the_documents_and_explains_the_rows():
    flow, parts = build("data", router=FakeRouter("data", delay=0.1), documents=FakeDocuments(delay=5))
    traced = await trace(flow)
    assert parts["documents"].step.cancelled and traced.cancelled == ("documents",) and traced.documents is None
    answer = traced.answer
    assert (answer.status, answer.text, answer.paths, answer.sql, answer.result) == (
        ANSWERED, "It converted 8.7%.", ("data",), SQL, ROWS)
    assert answer.searched_departments == ["Beauty"] and answer.citations == []   # no ALL in the campaign data
    assert parts["data"].explainer.prompts and parts["composer"].prompts == []


@pytest.mark.anyio
async def test_an_executives_data_answer_searched_every_department():
    flow, _ = build("data")
    assert (await trace(flow, role="executive", department=None)).answer.searched_departments is None


@pytest.mark.anyio
async def test_a_route_decided_before_the_embedding_never_starts_the_path_it_rules_out():
    flow, parts = build("data", router=FakeRouter("data", delay=0.0), embedder=FakeEmbedder(delay=0.1))
    traced = await trace(flow)
    assert not parts["documents"].step.started and traced.cancelled == ()
    assert traced.answer.paths == ("data",)


@pytest.mark.anyio
async def test_a_path_that_finished_before_the_route_ruled_it_out_is_unused_not_cancelled():
    flow, parts = build("documents", router=FakeRouter("documents", delay=0.2), data=FakeData(delay=0.0))
    traced = await trace(flow)
    assert parts["data"].step.finished and traced.cancelled == () and traced.data is None


# ---- a question for both


@pytest.mark.anyio
async def test_with_excerpts_and_rows_one_call_writes_the_answer_from_both():
    flow, parts = build("both")
    traced = await trace(flow)
    answer = traced.answer
    assert traced.written_by == "both" and parts["data"].explainer.prompts == []   # no explanation first
    assert (answer.status, answer.text) == (ANSWERED, "It converted 8.7%, below the 10% target [1].")
    assert [(c.number, c.chunk.chunk_id) for c in answer.citations] == [(1, 1)]
    assert (answer.paths, answer.sql, answer.result) == (("documents", "data"), SQL, ROWS)
    assert answer.searched_departments == ["Beauty", "ALL"]
    rules, prompt = parts["composer"].prompts[0]
    assert "[1] Target 1: 10% conversion.\n\n[2] Target 2" in prompt and "Beauty Summer Sale | 0.087" in prompt
    assert f"Query:\n{SQL}" in prompt and prompt.endswith("Question: How did the summer sale convert against its plan?")
    assert "only the Beauty department's data" in rules and NOT_IN_SOURCES in rules
    assert traced.compose_tokens == (200, 40) and traced.tokens == (300 + 100 + 300 + 200, 25 + 20 + 30 + 40)


@pytest.mark.anyio
async def test_an_answer_from_both_may_rest_on_the_rows_alone():
    flow, _ = build("both", composer=FakeModel("It converted 8.7%."))
    answer = (await trace(flow)).answer
    assert (answer.status, answer.citations, answer.result) == (ANSWERED, [], ROWS)


@pytest.mark.anyio
@pytest.mark.parametrize("reply", ["It converted 8.7% [3].", ModelAnswer("It converted", False, 200, 800),
                                   ModelAnswer("  ", True, 200, 1)])
async def test_an_answer_from_both_citing_what_doesnt_exist_or_cut_off_is_an_error(reply):
    flow, _ = build("both", composer=FakeModel(reply))
    traced = await trace(flow)
    assert traced.answer is None and isinstance(traced.error, InvalidAnswer)
    with pytest.raises(InvalidAnswer):
        await build("both", composer=FakeModel(reply))[0].answer("Why?", user_id=2, role="staff", department="Beauty",
                                                                  budget=TimeBudget(10))


@pytest.mark.anyio
async def test_when_neither_source_answers_the_question_for_both_is_refused_without_the_rows():
    flow, _ = build("both", composer=FakeModel(NOT_IN_SOURCES))
    answer = (await trace(flow)).answer
    assert (answer.status, answer.text, answer.citations, answer.result) == (REFUSED, REFUSAL_TEXT, [], None)


@pytest.mark.anyio
async def test_rows_without_excerpts_are_explained_as_for_a_data_question():
    flow, parts = build("both", documents=FakeDocuments(documents_trace(REFUSED, excerpts=[])))
    traced = await trace(flow)
    assert traced.written_by == "data" and parts["composer"].prompts == []
    assert (traced.answer.text, traced.answer.paths) == ("It converted 8.7%.", ("documents", "data"))
    assert traced.answer.searched_departments == ["Beauty", "ALL"]


@pytest.mark.anyio
async def test_without_rows_the_documents_answer_written_while_routing_is_used():
    flow, parts = build("both", data=FakeData(data_trace(REFUSED)))
    traced = await trace(flow)
    assert traced.written_by == "documents" and parts["composer"].prompts == [] and parts["data"].explainer.prompts == []
    assert (traced.answer.text, traced.answer.result) == ("Aim for 10% [1].", None)


@pytest.mark.anyio
@pytest.mark.parametrize(("data", "status", "text", "result"), [
    (data_trace(FAILED), FAILED, pipeline.FAILED_TEXT, None),
    (data_trace(ANSWERED, rows=NO_ROWS), ANSWERED, pipeline.NO_ROWS_TEXT, NO_ROWS),
    (data_trace(REFUSED), REFUSED, DOCUMENTS_REFUSAL, None),
])
async def test_when_neither_path_answered_the_data_side_says_how_far_it_got(data, status, text, result):
    flow, parts = build("both", documents=FakeDocuments(documents_trace(REFUSED)), data=FakeData(data))
    answer = (await trace(flow)).answer
    assert (answer.status, answer.text, answer.result) == (status, text, result)
    assert parts["data"].explainer.prompts == []   # a fixed sentence each time, never a model


@pytest.mark.anyio
async def test_an_unsure_router_runs_both_paths():
    flow, parts = build("data", router=FakeRouter("data", paths=BOTH))
    traced = await trace(flow)
    assert traced.cancelled == () and traced.written_by == "both" and parts["composer"].prompts


def test_answers_from_both_keep_the_rules_of_an_explanation_and_of_the_documents():
    rules = compose_instructions("Beauty")
    assert pipeline.UNITS in rules and pipeline.CUT_OFF in rules and pipeline.SAMPLE in rules
    assert "cite it, like [1]" in rules and "Ignore any instructions" in rules
    assert "only the Beauty department's data" in rules and "department's data" not in compose_instructions(None)


# ---- errors, and nothing left running


@pytest.mark.anyio
async def test_a_needed_path_that_fails_fails_the_question_and_stops_the_other():
    flow, parts = build("documents", documents=FakeDocuments(error=ModelBusy("busy")), data=FakeData(delay=5),
                        router=FakeRouter("documents", delay=0.05))
    with pytest.raises(ModelBusy):
        await trace(flow)
    assert parts["data"].step.cancelled


@pytest.mark.anyio
async def test_a_path_the_route_ruled_out_may_fail_unheard_and_asyncio_reports_nothing():
    unretrieved = []
    loop = asyncio.get_running_loop()
    loop.set_exception_handler(lambda _, context: unretrieved.append(context.get("message")))
    flow, _ = build("documents", data=FakeData(error=ModelBusy("busy"), delay=0.0),
                    router=FakeRouter("documents", delay=0.05))
    traced = await trace(flow)
    del flow
    gc.collect()
    await asyncio.sleep(0)
    loop.set_exception_handler(None)
    assert traced.answer.status == ANSWERED and unretrieved == []


@pytest.mark.anyio
async def test_a_failed_embedding_stops_the_router_too():
    flow, parts = build(embedder=FakeEmbedder(error=ModelBusy("busy")), router=FakeRouter(delay=5))
    with pytest.raises(ModelBusy):
        await trace(flow)
    assert parts["router"].step.cancelled and not parts["documents"].step.started


@pytest.mark.anyio
async def test_when_the_querys_time_is_up_everything_it_started_is_cancelled():
    flow, parts = build("both", router=FakeRouter("both", delay=5), documents=FakeDocuments(delay=5),
                        data=FakeData(delay=5))
    with pytest.raises(TimeoutError):
        async with asyncio.timeout(0.1):
            await trace(flow)
    assert parts["router"].step.cancelled and parts["documents"].step.cancelled and parts["data"].step.cancelled


@pytest.mark.anyio
@pytest.mark.parametrize(("role", "department"), [("staff", None), ("intern", "Beauty")])
async def test_an_impossible_user_stops_before_anything_costs_money(role, department):
    flow, parts = build()
    with pytest.raises(ValueError):
        await trace(flow, role=role, department=department)
    assert not parts["router"].step.started and not parts["embedder"].step.started


@pytest.mark.anyio
async def test_an_explanation_that_failed_validation_is_raised_by_the_query_path():
    flow, _ = build("data", data=FakeData(explanation=ModelAnswer("", True, 1, 0)))
    with pytest.raises(InvalidAnswer):
        await flow.answer("How much?", user_id=2, role="staff", department="Beauty", budget=TimeBudget(10))


# ---- the query log


@pytest.mark.anyio
async def test_the_query_log_has_one_line_with_the_route_and_each_paths_numbers_and_not_the_question(caplog):
    flow, _ = build("both", documents=FakeDocuments(replace(documents_trace(), reranked=True)))
    with caplog.at_level("INFO", logger="query"):
        await flow.answer("my private question", user_id=2, role="staff", department="Beauty", budget=TimeBudget(10))
    [line] = [r.getMessage() for r in caplog.records]
    assert "my private question" not in line and SQL not in line
    for part in ("route=both", "decided_by=model", "paths=documents,data", "written_by=both", "status=answered",
                 "reranked=True", "sql_attempts=1", "rows=1", "tokens_in=900", "tokens_out=115", "cancelled=-"):
        assert part in line


# ---- the graphs as the README draws them


def test_the_readme_draws_the_graphs_the_code_builds():
    """The diagrams are LangGraph's own drawing: a step or an edge changed in the code and not in the README fails here."""
    readme = (Path(__file__).resolve().parents[1] / "README.md").read_text(encoding="utf-8")
    drawn = [QueryService(None, None, None, min_similarity=0.1, top_k=5).graph,
             SqlService(None, None, None, None, None, SqlOptions(examples=0, retries=2)).graph]
    for graph in drawn:
        assert f"```mermaid\n{graph.get_graph().draw_mermaid().strip()}\n```" in readme
