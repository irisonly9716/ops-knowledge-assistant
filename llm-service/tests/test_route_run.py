"""How the routing evaluation scores: the nearest questions leave out a question's own group, and a missing path is told
apart from an extra one."""
from types import SimpleNamespace

from app.router.routing import BOTH, DATA, DOCUMENTS
from evaluation import route_run


def q(id_, route, group):
    return SimpleNamespace(id=id_, route=route, group=group)


def test_the_nearest_questions_leave_out_the_questions_own_group():
    questions = [q("a", "data", "g1"), q("b", "documents", "g1"), q("c", "data", "g2"), q("d", "documents", "g3")]
    vectors = [[1.0, 0.0], [0.99, 0.01], [0.8, 0.2], [0.0, 1.0]]
    vote = route_run.nearest(questions, vectors, k=1)[0]
    assert (vote.route, vote.neighbours) == ("data", ("c",))   # b is closer, but a paraphrase gives the answer away


def test_a_tie_goes_to_the_closer_voters():
    questions = [q("a", "both", "g1"), q("b", "data", "g2"), q("c", "documents", "g3")]
    vote = route_run.nearest(questions, [[1.0, 0.0], [0.6, 0.4], [0.9, 0.1]], k=2)[0]
    assert (vote.route, vote.agreement) == ("documents", 0.5)


def test_a_missing_path_and_an_extra_one_are_counted_apart():
    assert route_run.scored("both", frozenset({DOCUMENTS})) == (True, False)
    assert route_run.scored("data", BOTH) == (False, True)
    assert route_run.scored("data", frozenset({DOCUMENTS})) == (True, True)
    assert route_run.scored("none", frozenset({DOCUMENTS})) == (False, True)
    assert route_run.scored("documents", frozenset({DOCUMENTS})) == (False, False)


def test_the_cascade_asks_the_model_only_when_the_nearest_questions_disagree():
    record = {"nearest": {"route": "data", "agreement": 0.8},
              "model": {"route": "both", "confidence": 0.9, "decided_by": "model"}}
    assert route_run.cascade(record, 0.8) == ("data", frozenset({DATA}), False)
    assert route_run.cascade(record, 1.0) == ("both", BOTH, True)
