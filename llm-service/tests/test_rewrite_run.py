"""How the rewrite's evaluation groups and sums up questions, without a model."""
from evaluation import rewrite_run
from evaluation.dataset import Asker, Question, Turn, split_for


def question(text, turns=(), id_="q-r") -> Question:
    return Question(id=id_, question=text, turns=turns, asker=Asker(department="Beauty", role="staff"),
                    route="documents", expected="refuse", tags=("direct",), source="generated:test", group="g",
                    split=split_for(id_, "g", "refuse", "generated:test"))


def test_follow_ups_relative_words_and_the_rest_are_told_apart():
    assert rewrite_run.group(question("Who signs off?", (Turn(role="user", content="Who owns it?"),))) == "follow-up"
    assert rewrite_run.group(question("What did our latest launch make?")) == "relative word"
    assert rewrite_run.group(question("What should the host check before going live?")) == "other"


def test_the_summary_counts_each_group_and_shows_the_rewritten_ones():
    records = [
        {"id": "q-1", "question": "And the Beauty one?", "group": "follow-up", "turns": ["user: When did it run?"],
         "rewritten": "When did the Beauty Black Friday Live run?", "reason": "rewritten", "campaign": None,
         "period": None, "tokens": [800, 20], "ms": 900.0},
        {"id": "q-2", "question": "GMV of our last launch?", "group": "relative word", "turns": [],
         "rewritten": "GMV of {campaign}?", "reason": "rewritten", "period": None, "tokens": [800, 30], "ms": 1100.0,
         "campaign": {"words": "launch", "department": None, "position": 1}},
        {"id": "q-3", "question": "Who approves a refund?", "group": "other", "turns": [], "rewritten": None,
         "reason": "stands on its own", "campaign": None, "period": None, "tokens": [800, 10], "ms": 800.0},
    ]
    report = rewrite_run.summarize(records, "dev", "test")
    assert "| follow-up | 1 | 1 | 0 | 0 |  |" in report and "| other | 1 | 0 | 0 | 0 | stands on its own 1 |" in report
    assert "user: When did it run? / **And the Beauty one?**" in report and "campaign: launch, None, 1" in report
    assert "## other" not in report   # nothing in it was rewritten
