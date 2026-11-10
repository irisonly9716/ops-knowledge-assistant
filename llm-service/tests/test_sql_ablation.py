"""The ablation's reading of two runs a configuration: a step is credited with a question only when both runs agree on
both sides of it."""
from evaluation.sql_ablation import ADDING, FULL, TAKING_OUT, Measured, changed


def run(**exact: bool) -> dict[str, dict]:
    return {qid: {"id": qid, "question": qid, "route": "data", "exact": right} for qid, right in exact.items()}


def test_a_step_fixes_or_breaks_only_what_both_runs_agree_on():
    before = Measured([run(a=False, b=True, c=False, d=True, e=False), run(a=False, b=True, c=True, d=True, e=True)])
    after = Measured([run(a=True, b=False, c=True, d=True, e=False), run(a=True, b=False, c=True, d=False, e=False)])
    fixed, broke = changed(before, after)
    assert [r["id"] for r in fixed] == ["a"]    # c was right once before: noise, not the step
    assert [r["id"] for r in broke] == ["b"]    # d is wrong once after, e was right once before: noise too


def test_accuracy_is_per_run_and_counts_only_the_route_asked_for():
    first = run(a=True, b=False) | {"x": {"id": "x", "question": "x", "route": "both", "exact": False}}
    m = Measured([first, run(a=True, b=True)])
    assert m.accuracy("data") == [0.5, 1.0] and m.mean_accuracy("data") == 0.75 and m.accuracy("both")[0] == 0.0


def test_each_part_is_added_once_and_taken_out_once():
    """The last step before the full pipeline is the full pipeline without its retries."""
    assert FULL.flags == "--pipeline --dictionary --views --examples 3 --retries 2"
    assert TAKING_OUT[-1].runs == ADDING[-2].runs
    for c in TAKING_OUT[:3]:
        assert len(c.flags.split("--")) == len(FULL.flags.split("--")) - 1   # one switch fewer
