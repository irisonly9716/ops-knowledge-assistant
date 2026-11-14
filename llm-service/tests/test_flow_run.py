"""How the query path's evaluation records and sums up a question, with a fake flow: no network, no database."""
from dataclasses import replace
from datetime import date

import pytest

from app.query_flow import CLARIFY, FlowAnswer, FlowTrace
from app.router.rewriting import Rewritten
from app.router.rewriting import Turn as RouterTurn
from app.rag.answering import ANSWERED
from app.router.routing import PATHS, Routing
from app.sql_agent.execute import QueryResult
from app.sql_agent.campaigns import Campaign, Found
from app.sql_agent.pipeline import Attempt, SqlAnswer, SqlTrace
from evaluation import flow_run
from evaluation.dataset import Asker, Question, Turn, split_for

ROWS = QueryResult(("campaign_name", "total_gmv"), (("Beauty Summer Sale", "3000.50"),), truncated=False)


def question(route="data", gold=(("3000.50",),)) -> Question:
    return Question(id="q-flow", question="Why did the summer sale miss its target?",
                    asker=Asker(department="Beauty", role="staff"), route=route, expected="answer",
                    gold_sql="SELECT 1" if gold else None, gold_result=gold, answer_points=("its GMV",),
                    tags=("planning_advice",), source="generated:test", group="g1",
                    split=split_for("q-flow", "g1", "answer", "generated:test"))


def flow_trace(route="both", written_by="both", total_ms=2500.0, rows=ROWS) -> FlowTrace:
    data = SqlTrace(SqlAnswer(ANSWERED, "", "SELECT 2", rows, "Beauty"), None, (),
                    (Attempt("SELECT 2", "SELECT 2", None, 900.0, 5.0),), 0.0, 20.0, 0.0, (1000, 40), (0, 0))
    answer = FlowAnswer(ANSWERED, "It missed by $500 [1].", [], ["Beauty", "ALL"], ("documents", "data"), "SELECT 2",
                        rows)
    return FlowTrace(answer, None, Routing(route, 0.97, "x", PATHS[route], "model", 300, 25), written_by, None, data,
                     (), 200.0, 1200.0, 1500.0, 1000.0 if written_by == "both" else 0.0, (900, 60), total_ms)


class FakeFlow:
    def __init__(self, traced=None, error=None):
        self.traced, self.error, self.asked = traced, error, []

    async def trace(self, question, *, role, department, today, budget, turns=()):
        self.asked.append((question, role, department, today) + ((turns,) if turns else ()))
        if self.error:
            raise self.error
        return self.traced


@pytest.mark.anyio
async def test_a_question_records_its_route_its_times_its_cost_and_whether_its_rows_hold_the_gold_result():
    fake = FakeFlow(flow_trace())
    record = await flow_run.ask(fake, question(), 10)
    assert fake.asked == [("Why did the summer sale miss its target?", "staff", "Beauty", flow_run.AS_OF)]
    # It needed the data only: the documents ran for nothing
    assert (record["paths"], record["missing_path"], record["extra_path"]) == (["documents", "data"], False, True)
    assert (record["written_by"], record["status"], record["exact"], record["rows"]) == ("both", ANSWERED, True, 1)
    assert record["tokens"]["writer"] == [1000, 40] and record["tokens"]["compose"] == [900, 60]
    assert float(record["cost_usd"]) > 0


@pytest.mark.anyio
async def test_a_data_question_sent_to_the_documents_misses_a_path_and_has_no_rows_to_score():
    sent_away = replace(flow_trace(route="documents", written_by="documents"), data=None)
    record = await flow_run.ask(FakeFlow(sent_away), question(), 10)
    assert (record["missing_path"], record["rows"], record["exact"]) == (True, None, None)
    assert "a path missing" in flow_run.summarize([record], "dev", "test", 1.0)


@pytest.mark.anyio
async def test_a_question_that_runs_out_of_time_is_recorded_as_such():
    record = await flow_run.ask(FakeFlow(error=TimeoutError()), question(), 10)
    assert record["status"] == flow_run.TIMEOUT and "route" not in record


@pytest.mark.anyio
async def test_the_summary_counts_questions_over_the_target_by_the_paths_that_ran():
    fast = await flow_run.ask(FakeFlow(flow_trace(total_ms=2500.0)), question(), 10)
    slow = await flow_run.ask(FakeFlow(flow_trace(total_ms=3400.0)), question(), 10) | {"id": "q-slow"}
    report = flow_run.summarize([fast, slow], "dev", "test", 1.0)
    assert "| both | 2 | 1 (50.0%) | 2500 / 3400 / 3400 | 1200 / 1200 / 1200 |" in report
    assert "Over the target (1): `q-slow` 3400 ms (documents+data, written by both, 1 queries)" in report
    assert "their rows hold the gold result for 2 (100.0%)" in report
    assert "not used, because the answer came from both: 2 questions" in report


@pytest.mark.anyio
async def test_a_follow_up_is_asked_with_its_turns_and_a_question_asked_back_is_recorded_without_paths():
    follow_up = question().model_copy(update={"turns": (Turn(role="user", content="When did the summer sale run?"),
                                                        Turn(role="assistant", content="June to July."))})
    candidate = Campaign(7, "Beauty Black Friday Live", "Beauty", date(2025, 11, 18), date(2025, 12, 14))
    asked_back = FlowTrace(FlowAnswer(CLARIFY, "Which one?", [], None, (), None, None, None, (candidate,)), None, None,
                           "-", None, None, (), 200.0, 0.0, 0.0, 0.0, (0, 0), 1400.0,
                           Rewritten("GMV of {campaign}?", None, None, "rewritten", 200, 20), "GMV of {campaign}?",
                           Found(None, (candidate,), ()), 1300.0, False)
    fake = FakeFlow(asked_back)
    record = await flow_run.ask(fake, follow_up, 10)
    assert fake.asked[0][4] == (RouterTurn("user", "When did the summer sale run?"), RouterTurn("assistant", "June to July."))
    assert (record["status"], record["paths"], record["route"], record["missing_path"]) == (CLARIFY, [], None, False)
    assert record["candidates"] == [["Beauty", "Beauty Black Friday Live", "2025-11-18"]]
    assert record["tokens"]["rewriter"] == [200, 20] and record["tokens"]["router"] == [0, 0]
    report = flow_run.summarize([record], "dev", "test", 1.0)
    assert "Rewritten: 1 of 1" in report and "asked back: 1" in report
