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
from app.router.rewriting import CampaignReference, PeriodReference, Rewritten, Turn
from app.router.routing import BOTH, PATHS, Routing
from app.sql_agent.campaigns import EACH_DEPARTMENT_TEXT, NOT_FOUND_TEXT, SAME_DAY_TEXT, Campaign, Found
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

    async def route(self, question, budget, turns=()):
        return await self.step(question, turns=turns)


UNCHANGED = Rewritten(None, None, None, "stands on its own", 120, 8)


class FakeRewriter:
    def __init__(self, rewritten=UNCHANGED, delay=0.03):
        self.step = Recorded(delay, rewritten)

    async def rewrite(self, question, turns, budget):
        return await self.step(question, turns=turns)


LAST_BF = Campaign(7, "Beauty Black Friday Live", "Beauty", date(2025, 11, 18), date(2025, 12, 14))
BF_BEFORE = Campaign(3, "Beauty Black Friday Live", "Beauty", date(2024, 11, 18), date(2024, 12, 14))
GROCERY_BF = Campaign(9, "Grocery Black Friday Live", "Fresh Grocery", date(2025, 11, 12), date(2025, 12, 20))


class FakeCampaigns:
    def __init__(self, found=None, reviewed=frozenset()):
        self.found, self.reviewed = found or Found(LAST_BF, (), (BF_BEFORE,)), set(reviewed)
        self.asked, self.reviews_asked = [], []

    async def find(self, reference, *, role, department, budget):
        self.asked.append((reference, role, department))
        return self.found

    async def with_review(self, campaigns, budget):
        self.reviews_asked.append(campaigns)
        return {c.campaign_id for c in campaigns} & self.reviewed


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


def build(route="documents", *, documents=None, data=None, router=None, composer=None, embedder=None, rewriter=None,
          campaigns=None):
    parts = {"embedder": embedder or FakeEmbedder(), "router": router or FakeRouter(route),
             "rewriter": rewriter or FakeRewriter(), "documents": documents or FakeDocuments(),
             "data": data or FakeData(), "campaigns": campaigns or FakeCampaigns(),
             "composer": composer or FakeModel("It converted 8.7%, below the 10% target [1].")}
    return QueryFlow(parts["embedder"], parts["router"], parts["rewriter"], parts["documents"], parts["data"],
                     parts["campaigns"], parts["composer"]), parts


async def trace(flow, role="staff", department="Beauty", question="How did the summer sale convert against its plan?",
                turns=()):
    return await flow.trace(question, role=role, department=department, today=TODAY, budget=TimeBudget(10),
                            turns=turns)


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
    assert traced.compose_tokens == (200, 40)
    assert traced.tokens == (300 + 120 + 100 + 300 + 200, 25 + 8 + 20 + 30 + 40)   # the rewriter's 120 and 8 too


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
                 "reranked=True", "sql_attempts=1", "rows=1", "tokens_in=1020", "tokens_out=123", "cancelled=-",
                 "rewritten=False", "restarted=False", "basis=-"):
        assert part in line


# ---- rewriting: follow-ups, and campaigns and times named relative to now


FOLLOW_UP = (Turn("user", "Who owns the Beauty sale event inventory check?"),
             Turn("assistant", "The Beauty Supply Chain team owns the check."))


@pytest.mark.anyio
async def test_a_question_that_stands_on_its_own_goes_on_as_asked_and_the_router_sees_any_turns():
    flow, parts = build("documents")
    traced = await trace(flow, question="Who signs off on it?", turns=FOLLOW_UP)
    assert parts["rewriter"].step.calls[0] == (("Who signs off on it?",), {"turns": FOLLOW_UP})
    assert parts["router"].step.calls[0] == (("Who signs off on it?",), {"turns": FOLLOW_UP})
    assert not traced.restarted and traced.asked == "Who signs off on it?"
    assert len(parts["documents"].step.calls) == 1 and parts["campaigns"].asked == []


@pytest.mark.anyio
async def test_a_rewritten_follow_up_stops_the_paths_and_starts_them_again_on_the_question_that_stands_alone():
    standalone = "Who signs off on the Beauty sale event inventory check?"
    flow, parts = build("documents", rewriter=FakeRewriter(Rewritten(standalone, None, None, "rewritten", 150, 20),
                                                           delay=0.05),
                        documents=FakeDocuments(delay=0.2), data=FakeData(delay=0.2))
    traced = await trace(flow, question="Who signs off on it?", turns=FOLLOW_UP)
    first, second = parts["documents"].step.calls
    assert first[0] == ("Who signs off on it?",) and second[0] == (standalone,)
    assert [c[0] for c in parts["embedder"].step.calls] == [("Who signs off on it?",), (standalone,)]
    assert parts["data"].step.cancelled   # started on the question as asked, stopped by the rewrite
    assert traced.restarted and traced.asked == standalone and traced.answer.basis is None
    assert traced.answer.text == "Aim for 10% [1]."   # no campaign, so no line saying which one


@pytest.mark.anyio
async def test_a_campaign_named_by_its_place_in_time_is_found_filled_in_and_named_first():
    rewritten = Rewritten("What was the GMV of {campaign}?", CampaignReference(words="Black Friday", department=None,
                                                                               position=1), None, "rewritten")
    flow, parts = build("data", rewriter=FakeRewriter(rewritten))
    traced = await trace(flow, question="What was the GMV of our last Black Friday?")
    reference, role, department = parts["campaigns"].asked[0]
    assert (reference.words, role, department) == ("Black Friday", "staff", "Beauty")
    asked = "What was the GMV of Beauty Black Friday Live (the campaign that ran from 2025-11-18 to 2025-12-14)?"
    assert traced.asked == asked and parts["data"].step.calls[-1][0] == (asked,)
    assert traced.answer.text == ("Based on Beauty Black Friday Live, which ran from 2025-11-18 to 2025-12-14.\n\n"
                                  "It converted 8.7%.")
    assert traced.answer.basis == LAST_BF
    assert parts["campaigns"].reviews_asked == []   # only the data was searched: whether it has a review doesn't matter


@pytest.mark.anyio
async def test_when_the_documents_are_searched_a_campaign_without_a_review_says_so_and_names_the_one_before():
    rewritten = Rewritten("What did the review of {campaign} say?", CampaignReference(words="Black Friday",
                                                                                      department=None, position=1),
                          None, "rewritten")
    flow, parts = build("documents", rewriter=FakeRewriter(rewritten), campaigns=FakeCampaigns(reviewed={3}))
    text = (await trace(flow)).answer.text
    assert text.startswith("Based on Beauty Black Friday Live, which ran from 2025-11-18 to 2025-12-14. It has no "
                           "post-campaign review yet; the latest one before it that has one is Beauty Black Friday "
                           "Live, which ran from 2024-11-18 to 2024-12-14.\n\n")
    assert parts["campaigns"].reviews_asked == [(LAST_BF, BF_BEFORE)]


@pytest.mark.anyio
async def test_a_campaign_with_a_review_gets_the_basis_alone():
    rewritten = Rewritten("What did the review of {campaign} say?", CampaignReference(words=None, department=None,
                                                                                      position=1), None, "rewritten")
    flow, _ = build("documents", rewriter=FakeRewriter(rewritten), campaigns=FakeCampaigns(reviewed={7}))
    assert "review" not in (await trace(flow)).answer.text.split("\n\n")[0]


@pytest.mark.anyio
@pytest.mark.parametrize(("found", "status", "text"), [
    (Found(None, (LAST_BF, GROCERY_BF), ()), "clarify", EACH_DEPARTMENT_TEXT),
    (Found(None, (LAST_BF, replace(LAST_BF, campaign_id=8, name="Beauty Holiday Live")), ()), "clarify", SAME_DAY_TEXT),
    (Found(None, (), ()), REFUSED, NOT_FOUND_TEXT),
])
async def test_a_campaign_asked_back_or_not_found_runs_no_path_and_stops_the_router(found, status, text):
    rewritten = Rewritten("What was the GMV of {campaign}?", CampaignReference(words="Black Friday", department=None,
                                                                               position=1), None, "rewritten")
    flow, parts = build("data", rewriter=FakeRewriter(rewritten), router=FakeRouter("data", delay=5),
                        data=FakeData(delay=5), documents=FakeDocuments(delay=5), campaigns=FakeCampaigns(found))
    traced = await trace(flow, role="executive", department=None)
    answer = traced.answer
    assert (answer.status, answer.text, answer.paths, answer.result, answer.basis) == (status, text, (), None, None)
    assert answer.candidates == found.candidates and answer.searched_departments is None
    assert parts["router"].step.cancelled and parts["data"].step.cancelled and len(parts["data"].step.calls) == 1
    assert traced.routing is None and traced.total_ms < 1000


@pytest.mark.anyio
async def test_a_time_relative_to_now_is_filled_in_with_dates_worked_out_from_today():
    rewritten = Rewritten("What was our GMV in {period}?", None, PeriodReference(kind="last_month", months=None),
                          "rewritten")
    flow, parts = build("data", rewriter=FakeRewriter(rewritten))
    traced = await trace(flow)
    assert traced.asked == "What was our GMV in September 2026 (2026-09-01 to 2026-09-30)?"   # TODAY is in October
    assert parts["data"].step.calls[-1][1]["today"] == TODAY and parts["campaigns"].asked == []


# ---- the graphs as the README draws them


def test_the_readme_draws_the_graphs_the_code_builds():
    """The diagrams are LangGraph's own drawing: a step or an edge changed in the code and not in the README fails here."""
    readme = (Path(__file__).resolve().parents[1] / "README.md").read_text(encoding="utf-8")
    drawn = [QueryService(None, None, None, min_similarity=0.1, top_k=5).graph,
             SqlService(None, None, None, None, None, SqlOptions(examples=0, retries=2)).graph]
    for graph in drawn:
        assert f"```mermaid\n{graph.get_graph().draw_mermaid().strip()}\n```" in readme
