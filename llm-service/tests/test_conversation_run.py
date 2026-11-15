"""The conversation evaluation: its two question files, how a question is checked against what it should come to, and
what counts as a leak. A fake flow stands in for the query path; the label check runs on a throwaway database."""
from datetime import date

import pytest
from pydantic import ValidationError
from postgres_helpers import postgres_available, throwaway_database

from app import db
from app.query_flow import CLARIFY, FlowAnswer, FlowTrace
from app.rag.answering import ANSWERED, REFUSED
from app.router.rewriting import Rewritten
from app.sql_agent.campaigns import NOT_FOUND_TEXT, Campaign
from app.sql_agent.execute import QueryResult
from evaluation import conversation_run
from evaluation.conversation_run import KINDS, QUESTION_FILES, Conversation, checked, label_problems, load

BEAUTY_ANNIVERSARY = Campaign(10, "Beauty Anniversary Sale", "Beauty", date(2026, 9, 7), date(2026, 10, 8))
HOME_ANNIVERSARY = Campaign(50, "Home Anniversary Sale", "Home Goods", date(2026, 9, 4), date(2026, 9, 24))
UNCHANGED = Rewritten(None, None, None, "stands on its own", 120, 8)


def conversation(expect: dict, kind="last_campaign", asker=None, turns=()) -> Conversation:
    return Conversation.model_validate({"id": "conv-t", "asker": asker or {"department": "Beauty", "role": "staff"},
                                        "turns": list(turns), "question": "What was the GMV of our last campaign?",
                                        "kind": kind, "expect": expect, "source": "written:test"})


def basis_label(campaign: Campaign) -> dict:
    return {"basis": {"department": campaign.department, "name": campaign.name, "start": campaign.start_date.isoformat()}}


def trace(status=ANSWERED, basis=None, candidates=(), text="It made more than planned.", rewritten=UNCHANGED,
          asked="What was the GMV of our last campaign?", rows=None) -> FlowTrace:
    answer = FlowAnswer(status, text, [], ["Beauty"], ("data",) if status != CLARIFY else (), None, rows, basis,
                        tuple(candidates))
    return FlowTrace(answer, None, None, "data", None, None, (), 0.0, 0.0, 0.0, 0.0, (0, 0), 2100.0, rewritten, asked,
                     None, 1100.0, rewritten.changed)


# ---- the question files


@pytest.mark.parametrize("questions", QUESTION_FILES)
def test_each_file_holds_every_kind_of_question_with_ids_of_its_own(questions):
    conversations = load(QUESTION_FILES[questions])
    assert {c.kind for c in conversations} == set(KINDS)
    assert len(conversations) >= 15
    other = load(QUESTION_FILES["check" if questions == "dev" else "dev"])
    assert not {c.question for c in conversations} & {c.question for c in other}   # nothing tuned on is checked on


@pytest.mark.parametrize(("record", "problem"), [
    ({"kind": "last_campaign", "expect": {"basis": {"department": "Beauty", "name": "x", "start": "2026-01-01"},
                                          "refused": True}}, "exactly one thing"),
    ({"kind": "ask_back", "expect": {"refused": True}}, "can't expect refused"),
    ({"kind": "follow_up", "expect": {"asked_contains": ["it"]}}, "needs the turns"),
    ({"kind": "last_campaign", "expect": {"basis": {"department": "Home Goods", "name": "x", "start": "2026-01-01"}}},
     "one the asker may see"),
    ({"kind": "ask_back", "expect": {"clarify": ["Beauty", "Home Goods"]}}, "only executives and admins"),
])
def test_a_label_that_cant_be_right_is_refused_on_load(record, problem):
    with pytest.raises(ValidationError, match=problem):
        Conversation.model_validate({"id": "conv-t", "asker": {"department": "Beauty", "role": "staff"},
                                     "question": "What was the GMV of our last campaign?", "source": "written:test",
                                     **record})


# ---- checked against what it should come to


def test_the_basis_must_be_the_campaign_labelled_down_to_its_start_date():
    expected = conversation(basis_label(BEAUTY_ANNIVERSARY))
    assert checked(expected, trace(basis=BEAUTY_ANNIVERSARY), set()) == (True, [])
    earlier = Campaign(9, "Beauty Anniversary Sale", "Beauty", date(2025, 9, 7), date(2025, 10, 8))
    assert checked(expected, trace(basis=earlier), set())[0] is False
    assert checked(expected, trace(basis=None), set())[0] is False


def test_a_follow_up_may_name_the_campaign_an_answer_already_found_instead_of_looking_it_up_again():
    expected = conversation(basis_label(BEAUTY_ANNIVERSARY), kind="follow_up",
                            turns=[{"role": "user", "content": "What was the GMV of our last campaign?"},
                                   {"role": "assistant", "content": "Based on Beauty Anniversary Sale, which ran from "
                                                                    "2026-09-07 to 2026-10-08."}])
    written = "How many units did Beauty Anniversary Sale (the campaign that ran from 2026-09-07 to 2026-10-08) sell?"
    rewritten = Rewritten(written, None, None, "rewritten", 900, 30)
    assert checked(expected, trace(rewritten=rewritten, asked=written), set())[0]
    other_year = written.replace("2026-09-07", "2025-09-07")
    assert not checked(expected, trace(rewritten=rewritten, asked=other_year), set())[0]
    assert not checked(expected, trace(asked=written), set())[0]   # not rewritten: the question already said it


def test_asking_back_must_offer_one_candidate_from_each_department_named():
    expected = conversation({"clarify": ["Beauty", "Home Goods"]}, kind="ask_back",
                            asker={"department": None, "role": "executive"})
    assert checked(expected, trace(CLARIFY, candidates=(BEAUTY_ANNIVERSARY, HOME_ANNIVERSARY)), set())[0]
    assert not checked(expected, trace(CLARIFY, candidates=(BEAUTY_ANNIVERSARY,)), set())[0]
    assert not checked(expected, trace(basis=BEAUTY_ANNIVERSARY), set())[0]


def test_another_departments_campaign_must_be_refused_about_no_campaign():
    expected = conversation({"refused": True}, kind="another_department")
    assert checked(expected, trace(REFUSED, text=NOT_FOUND_TEXT), set())[0]
    assert not checked(expected, trace(ANSWERED), set())[0]


def test_a_question_to_leave_as_asked_must_not_be_rewritten():
    expected = conversation({"as_asked": True}, kind="as_asked")
    assert checked(expected, trace(), set())[0]
    rewritten = Rewritten("What was the GMV of {campaign}?", None, None, "rewritten", 120, 20)
    assert not checked(expected, trace(rewritten=rewritten), set())[0]


def test_a_follow_up_written_out_must_hold_every_word_labelled_whatever_its_case():
    expected = conversation({"asked_contains": ["inventory check", "Beauty"]}, kind="follow_up",
                            turns=[{"role": "user", "content": "Who owns the Beauty sale event inventory check?"},
                                   {"role": "assistant", "content": "The Supply Chain team."}])
    rewritten = Rewritten("Who signs off on the beauty sale event Inventory Check?", None, None, "rewritten", 120, 20)
    assert checked(expected, trace(rewritten=rewritten, asked=rewritten.standalone), set())[0]
    assert not checked(expected, trace(rewritten=rewritten, asked="Who signs off on the inventory check?"), set())[0]
    assert not checked(expected, trace(asked="Who signs off on the Beauty inventory check?"), set())[0]   # unchanged


def test_a_campaign_of_a_department_the_asker_may_not_see_is_a_leak_wherever_it_shows():
    expected = conversation({"refused": True}, kind="another_department")
    hidden = {"Home Anniversary Sale"}
    assert checked(expected, trace(basis=HOME_ANNIVERSARY), hidden)[1][0] == "basis Home Anniversary Sale"
    assert checked(expected, trace(CLARIFY, candidates=(HOME_ANNIVERSARY,)), hidden)[1] == [
        "candidate Home Anniversary Sale"]
    assert checked(expected, trace(REFUSED, text="Home Anniversary Sale isn't yours."), hidden)[1] == [
        "names Home Anniversary Sale"]
    rows = QueryResult(("campaign_name",), (("Home Anniversary Sale",),), truncated=False)
    assert checked(expected, trace(rows=rows), hidden)[1] == ["names Home Anniversary Sale"]
    assert checked(expected, trace(REFUSED, text=NOT_FOUND_TEXT), hidden) == (True, [])


# ---- a run


class FakeFlow:
    def __init__(self, traced=None, error=None):
        self.traced, self.error, self.asked = traced, error, []

    async def trace(self, question, *, role, department, today, budget, turns=()):
        self.asked.append((question, role, department, today, turns))
        if self.error:
            raise self.error
        return self.traced


@pytest.mark.anyio
async def test_a_question_is_asked_as_of_the_evaluations_date_with_its_turns_and_recorded_with_its_cost():
    follow_up = conversation(basis_label(BEAUTY_ANNIVERSARY), kind="follow_up",
                             turns=[{"role": "user", "content": "What was our last campaign?"},
                                    {"role": "assistant", "content": "Based on Beauty Anniversary Sale."}])
    rewritten = Rewritten("How many units did {campaign} sell?", None, None, "rewritten", 900, 30)
    fake = FakeFlow(trace(basis=BEAUTY_ANNIVERSARY, rewritten=rewritten,
                          asked="How many units did Beauty Anniversary Sale sell?"))
    record = await conversation_run.ask(fake, follow_up, set(), 10)
    assert fake.asked[0][3] == conversation_run.AS_OF and len(fake.asked[0][4]) == 2
    assert (record["right"], record["leaks"], record["basis"]) == (True, [], ["Beauty", "Beauty Anniversary Sale",
                                                                              "2026-09-07"])
    assert record["tokens"]["rewriter"] == [900, 30] and float(record["cost_usd"]) > 0


@pytest.mark.anyio
async def test_a_question_past_its_time_counts_as_not_right():
    record = await conversation_run.ask(FakeFlow(error=TimeoutError()), conversation({"as_asked": True},
                                                                                     kind="as_asked"), set(), 10)
    assert (record["status"], record["right"]) == (conversation_run.TIMEOUT, False)


@pytest.mark.anyio
async def test_the_summary_counts_right_answers_by_kind_and_shows_every_leak():
    right = await conversation_run.ask(FakeFlow(trace(basis=BEAUTY_ANNIVERSARY)),
                                       conversation(basis_label(BEAUTY_ANNIVERSARY)), set(), 10)
    leaked = await conversation_run.ask(FakeFlow(trace(REFUSED, text="Home Anniversary Sale did well.")),
                                        conversation({"refused": True}, kind="another_department"),
                                        {"Home Anniversary Sale"}, 10) | {"id": "conv-leak"}
    report = conversation_run.summarize([right, leaked], "dev", "t", 1.0)
    assert "**Right: 2 of 2** (100.0%). **Leaks: 1** in 1 questions." in report
    assert "| last_campaign | 1 | 1 |" in report and "| another_department | 1 | 1 |" in report
    assert "LEAK: names Home Anniversary Sale" in report


# ---- the labels against the data


def test_a_label_naming_a_campaign_that_doesnt_exist_or_hasnt_finished_is_caught_before_the_run():
    if not postgres_available():
        pytest.skip("needs the docker compose Postgres")
    with throwaway_database() as name, db.connect(name) as conn:
        conn.execute("INSERT INTO departments (name) VALUES ('Beauty')")
        conn.execute("INSERT INTO hosts (name, department_id, category) VALUES ('Tommy Walter', 1, 'Beauty')")
        conn.execute("""INSERT INTO campaigns (name, department_id, host_id, start_date, end_date, status,
                                               planned_operating_cost, actual_operating_cost) VALUES
                        ('Beauty Anniversary Sale', 1, 1, '2026-09-07', '2026-10-08', 'finished', 10, 12),
                        ('Beauty Weekly Live Special', 1, 1, '2026-09-28', '2026-11-03', 'running', 10, NULL)""")
        running = Campaign(11, "Beauty Weekly Live Special", "Beauty", date(2026, 9, 28), date(2026, 11, 3))
        missing = Campaign(12, "Beauty Spring Sale", "Beauty", date(2026, 4, 1), date(2026, 4, 30))
        labels = [conversation(basis_label(c)).model_copy(update={"id": f"conv-{c.campaign_id}"})
                  for c in (BEAUTY_ANNIVERSARY, running, missing)]
        assert label_problems(conn, labels) == [
            "conv-11: Beauty Weekly Live Special (2026-09-28) hadn't finished by 2026-10-15",
            "conv-12: no Beauty campaign Beauty Spring Sale (2026-04-01)"]
