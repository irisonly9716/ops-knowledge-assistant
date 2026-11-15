"""Composing the same inputs again at each reasoning effort or with other instructions: which questions are taken,
what the judge is given, how a refusal counts, and the rules that decide. Fake models and a fake flow; nothing is graded
here."""
from datetime import date

import httpx
import openai
import pytest

from app.query_flow import FlowAnswer, FlowTrace, NOT_IN_SOURCES
from app.rag.answering import ANSWERED, REFUSED, QueryAnswer, QueryTrace
from app.rag.query_models import ModelAnswer
from app.rag.retrieval import RetrievedChunk
from app.router.routing import PATHS, Routing
from app.sql_agent.execute import QueryResult
from app.sql_agent.pipeline import Attempt, SqlAnswer, SqlTrace
from evaluation import compose_run
from evaluation.compose_run import (ERROR, INVALID, Inputs, Variant, coverage_counting_misses, effort_numbers,
                                   instructions_verdict, verdict)
from evaluation.dataset import Asker, Question, split_for

CHUNK = RetrievedChunk(chunk_id=1, doc_id=11, doc_type="campaign_plan", title="Plan", department="Beauty",
                       heading_path=("Plan", "Targets"), content="Target: 10% conversion.", score=0.6)
ROWS = QueryResult(("campaign_name", "avg_conversion_rate"), (("Beauty Summer Sale", "0.087"),), truncated=False)
DATA = SqlTrace(SqlAnswer(ANSWERED, "", "SELECT 1", ROWS, "Beauty"), None, (), (Attempt("SELECT 1", "SELECT 1", None,
                                                                                         900.0, 5.0),),
                0.0, 1.0, 0.0, (1000, 40), (0, 0))


def question(qid="q-both") -> Question:
    return Question(id=qid, question="How did the summer sale convert against its plan?",
                    asker=Asker(department="Beauty", role="staff"), route="data", expected="answer",
                    gold_sql="SELECT 1", gold_result=(("0.087",),),
                    answer_points=("it converted 8.7%", "below the 10% target"), tags=("planning_advice",),
                    source="generated:test", group="g1", split=split_for(qid, "g1", "answer", "generated:test"))


def inputs(qid="q-both") -> Inputs:
    return Inputs(question(qid), "How did the summer sale convert against its plan?", [CHUNK], DATA, ["Beauty", "ALL"],
                  ("documents", "data"))


class FakeComposer:
    def __init__(self, *replies):
        self.replies, self.prompts, self.instructions = list(replies), [], []

    async def answer(self, instructions, prompt, budget):
        self.prompts.append(prompt)
        self.instructions.append(instructions)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply if isinstance(reply, ModelAnswer) else ModelAnswer(reply, True, 1200, 90)


def flow_trace(written_by="both") -> FlowTrace:
    documents = QueryTrace(QueryAnswer(ANSWERED, "Aim for 10% [1].", [], ["Beauty", "ALL"]), None, False, [CHUNK],
                           False, 0.6, 0.0, 1.0, 0.0, 1.0, 100, 20, [CHUNK])
    answer = FlowAnswer(ANSWERED, "It converted 8.7% [1].", [], ["Beauty", "ALL"], ("documents", "data"), "SELECT 1",
                        ROWS)
    return FlowTrace(answer, None, Routing("both", 0.97, "x", PATHS["both"], "model"), written_by, documents, DATA, (),
                     0.0, 0.0, 0.0, 0.0, (0, 0), 0.0, asked="How did the summer sale convert against its plan?")


class FakeFlow:
    def __init__(self, traced=None, error=None):
        self.traced, self.error = traced, error

    async def trace(self, question, *, role, department, today, budget, turns=()):
        if self.error:
            raise self.error
        return self.traced


# ---- which questions, and what they are written from


def test_the_questions_are_those_a_whole_path_run_wrote_from_both():
    assert compose_run.composed_ids([{"id": "a", "written_by": "both"}, {"id": "b", "written_by": "data"},
                                     {"id": "c", "status": "timeout"}]) == ["a"]


@pytest.mark.anyio
async def test_the_inputs_are_what_the_query_path_wrote_the_answer_from_or_why_there_are_none_this_time():
    taken = await compose_run.inputs_of(FakeFlow(flow_trace()), question(), 10)
    assert (taken.excerpts, taken.data, taken.ran) == ([CHUNK], DATA, ("documents", "data"))
    assert await compose_run.inputs_of(FakeFlow(flow_trace("documents")), question(), 10) == \
        "written by documents this time"
    failed = openai.APIConnectionError(request=httpx.Request("POST", "https://api.test"))
    assert await compose_run.inputs_of(FakeFlow(error=failed), question(), 10) == "APIConnectionError"


# ---- composing again


@pytest.mark.anyio
async def test_the_judge_gets_the_rows_as_one_more_excerpt_since_the_answer_rests_on_them_too():
    composer = FakeComposer("It converted 8.7%, below the 10% target [1].")
    record = await compose_run.compose(Variant("low", "low", composer), inputs(), 1, 10)
    assert (record["status"], record["variant"], record["effort"], record["repeat"], record["tokens"]) == (
        ANSWERED, "low", "low", 1, [1200, 90])
    assert record["excerpts"][0]["content"] == "Target: 10% conversion."
    assert record["excerpts"][1]["content"].startswith("Result of a database query on the campaign data:\n")
    assert "Beauty Summer Sale | 0.087" in record["excerpts"][1]["content"]
    assert "Beauty Summer Sale | 0.087" in composer.prompts[0]   # the same prompt the query path writes


@pytest.mark.anyio
@pytest.mark.parametrize(("reply", "status"), [
    (NOT_IN_SOURCES, REFUSED),
    ("It converted 8.7% [4].", INVALID),
    (ModelAnswer("It converted", False, 1200, 800), INVALID),   # cut off: reasoning shares the output limit
    (openai.APIConnectionError(request=httpx.Request("POST", "https://api.test")), ERROR),
])
async def test_a_refusal_an_answer_that_fails_validation_and_a_failed_call_are_recorded_as_such(reply, status):
    assert (await compose_run.compose(Variant("none", "none", FakeComposer(reply)), inputs(), 1, 10))["status"] == status


@pytest.mark.anyio
async def test_every_variant_composes_each_question_in_turn_and_drops_out_after_its_last_repeat():
    variants = [Variant("none", "none", FakeComposer(*["a [1]."] * 4), repeats=2),
                Variant("none, earlier", "none", FakeComposer(*["b [1]."] * 2), "Be short.\n{department_scope}",
                        repeats=1)]
    records = await compose_run.compose_all(variants, [inputs("q-1"), inputs("q-2")], 10)
    assert [(r["repeat"], r["id"], r["variant"]) for r in records] == [
        (1, "q-1", "none"), (1, "q-1", "none, earlier"), (1, "q-2", "none"), (1, "q-2", "none, earlier"),
        (2, "q-1", "none"), (2, "q-2", "none")]


@pytest.mark.anyio
async def test_a_files_instructions_get_the_askers_department_rule_and_the_codes_are_used_otherwise():
    composer = FakeComposer("a [1].", "b [1].")
    await compose_run.compose(Variant("none, earlier", "none", composer, "Rules:\n{department_scope}- Be short."),
                              inputs(), 1, 10)
    await compose_run.compose(Variant("none", "none", composer), inputs(), 1, 10)
    earlier, code = composer.instructions
    assert earlier.startswith("Rules:\n- The result covers only the Beauty department's data") and \
        earlier.endswith("- Be short.")
    assert code == compose_run.compose_instructions("Beauty")


# ---- the numbers and the rule


def graded(variant, repeat, status=ANSWERED, coverage=1.0, faithfulness=1.0, ms=1500.0, points=("x",)) -> dict:
    record = {"id": f"q-{repeat}", "variant": variant, "effort": variant.split(", ")[0], "repeat": repeat,
              "status": status, "ms": ms, "tokens": [1000, 100], "answer_points": list(points), "question": "q"}
    return record | ({"coverage": coverage, "faithfulness": faithfulness} if status == ANSWERED else {})


def test_a_refusal_counts_as_no_point_said_and_a_question_without_points_has_no_coverage():
    assert coverage_counting_misses(graded("none", 1, status=REFUSED)) == 0.0
    assert coverage_counting_misses(graded("none", 1, status=INVALID)) == 0.0
    assert coverage_counting_misses(graded("none", 1, coverage=0.5)) == 0.5
    assert coverage_counting_misses(graded("none", 1, points=())) is None
    assert coverage_counting_misses({**graded("none", 1), "coverage": None}) is None   # its grading failed


def numbers(effort, coverages, faithfulness=0.9, ms=1500.0) -> dict:
    return effort_numbers([graded(effort, repeat, coverage=c, faithfulness=faithfulness, ms=ms)
                           for repeat, c in enumerate(coverages, 1)])


@pytest.mark.parametrize(("low", "replaces", "failed"), [
    (numbers("low", [0.80, 0.82], ms=2000.0), True, None),
    (numbers("low", [0.74, 0.76], ms=2000.0), False, "coverage +3.0 points"),
    (numbers("low", [0.80, 0.82], faithfulness=0.85), False, "faithfulness -5.0 points"),
    (numbers("low", [0.80, 0.82], ms=2600.0), False, "p50 +1100 ms"),
])
def test_low_replaces_none_only_when_every_condition_set_before_the_run_holds(low, replaces, failed):
    none = numbers("none", [0.71, 0.73])
    decided, conditions = verdict(none, low)
    assert decided is replaces
    if failed:
        assert any(c.startswith("not met") and failed in c for c in conditions)


def test_a_gain_no_larger_than_none_differs_from_itself_isnt_enough():
    none = numbers("none", [0.60, 0.72])   # 12 points apart between its own repeats
    low = numbers("low", [0.71, 0.73])     # 6 points above none's mean
    decided, conditions = verdict(none, low)
    assert not decided and "not met: none differs from itself by 12.0 points between repeats" in conditions


def test_the_summary_shows_each_effort_its_repeats_and_the_rule_as_it_came_out():
    records = [graded("none", r, coverage=c) for r, c in ((1, 0.70), (2, 0.72))]
    records += [graded("low", r, coverage=c, ms=2100.0) for r, c in ((1, 0.80), (2, 0.82))]
    report = compose_run.summarize(records, {"q-gone": "written by documents this time"}, ["none", "low"], "dev", "t",
                                   "dev-flow-3", compose_run.Decimal("0.21"))
    assert "| none | 2 | 2 / 0 / 0 | 71.0% | 70.0% / 72.0% |" in report
    assert "| low | 2 | 2 / 0 / 0 | 81.0% | 80.0% / 82.0% |" in report
    assert "**The rule: low replaces none.**" in report
    assert "`q-gone` (written by documents this time)" in report and "grading $0.2100" in report


@pytest.mark.parametrize(("code", "kept", "failed"), [
    (numbers("none", [0.80, 0.84]), True, None),
    (numbers("none", [0.62, 0.66]), False, "coverage +1.5 points"),   # within the 4 points they spread over
    (numbers("none", [0.80, 0.84], faithfulness=0.85), False, "faithfulness -5.0 points"),
])
def test_new_instructions_are_kept_only_above_the_noise_and_without_losing_faithfulness(code, kept, failed):
    earlier = numbers("none, earlier", [0.625], faithfulness=0.9)
    decided, conditions = instructions_verdict(code, earlier)
    assert decided is kept
    if failed:
        assert any(c.startswith("not met") and failed in c for c in conditions)


def test_the_summary_compares_the_codes_instructions_with_the_files_and_shows_both():
    records = [graded("none", r, coverage=c) for r, c in ((1, 0.84), (2, 0.86))]
    records += [graded("none, earlier", 1, coverage=0.62)]
    report = compose_run.summarize(records, {}, ["none", "none, earlier"], "dev", "t", "dev-flow-3",
                                   compose_run.Decimal("0.5"), {"none, earlier": "Rules:\n{department_scope}- Be short."})
    assert "| none, earlier | 1 | 1 / 0 / 0 | 62.0% | 62.0% |" in report
    assert "**The rule: the code's instructions replace earlier's at none.**" in report
    assert "Answer every part of the question" in report and "- Be short." in report   # both sets, for the record


def test_an_earlier_runs_answers_are_compared_on_the_questions_both_runs_composed():
    old = [{**graded("none", r, coverage=c), "id": qid} for qid, r, c in
           (("q-a", 1, 0.5), ("q-a", 2, 0.5), ("q-b", 1, 0.6), ("q-b", 2, 0.6), ("q-gone", 1, 0.0), ("q-gone", 2, 0.0))]
    for record in old:
        del record["variant"]   # a run from before variants had only efforts
    earlier = compose_run.earlier_variant(old, "dev-compose-1", "none")
    assert {r["variant"] for r in earlier} == {"none, dev-compose-1"}
    now = [{**graded("none", 1, coverage=c), "id": qid} for qid, c in (("q-a", 0.9), ("q-b", 1.0), ("q-new", 1.0))]
    report = compose_run.summarize(now, {}, ["none"], "dev", "t", "dev-flow-3", compose_run.Decimal("0.3"),
                                   earlier=earlier)
    assert "Against none, dev-compose-1, on the 2 questions both runs composed:" in report
    assert "| none | 2 | 95.0% | 95.0% |" in report and "| none, dev-compose-1 | 4 | 55.0% | 55.0% / 55.0% |" in report
    assert "**The rule: the code's instructions replace none, dev-compose-1's.**" in report
    assert "| `q-a` q | 90.0% | 50.0% / 50.0% |" in report and "q-gone" not in report
