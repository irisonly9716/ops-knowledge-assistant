"""The numbers of an evaluation run: what counts as found, as a wrong answer, and which threshold wins."""
import pytest

from evaluation import metrics
from evaluation.metrics import ANSWERED, ERROR, REFUSED, Outcome


# ---- retrieval


def test_a_section_is_found_at_the_rank_of_its_best_chunk():
    assert metrics.section_ranks([7, 3, 9, 4], [[4, 3], [9], [11]]) == [2, 3, None]


def test_recall_counts_sections_not_chunks():
    ranks = [1, 6, None]   # one in the top 5, one in the top 50, one nowhere
    assert metrics.recall_at(ranks, 5) == pytest.approx(1 / 3)
    assert metrics.recall_at(ranks, 50) == pytest.approx(2 / 3)


def test_rank_k_itself_is_inside_the_first_k():
    assert metrics.recall_at([5], 5) == 1
    assert metrics.recall_at([6], 5) == 0


def test_the_reciprocal_rank_is_that_of_the_first_section_found():
    assert metrics.reciprocal_rank([4, 2, None]) == 0.5
    assert metrics.reciprocal_rank([None, None]) == 0


def test_a_question_without_sections_has_no_recall():
    with pytest.raises(ValueError):
        metrics.recall_at([], 5)


def test_scores_are_averaged_over_questions():
    scores = metrics.retrieval_scores([[1], [None], [3, None]])
    assert scores.questions == 3
    assert scores.recall_at_5 == pytest.approx((1 + 0 + 0.5) / 3)
    assert scores.mrr == pytest.approx((1 + 0 + 1 / 3) / 3)
    assert metrics.retrieval_scores([]) is None


# ---- refusals


def test_below_the_threshold_is_refused_and_at_it_the_run_stands():
    outcome = Outcome("answer", ANSWERED, 0.40)
    assert metrics.status_at(outcome, 0.41) == REFUSED
    assert metrics.status_at(outcome, 0.40) == ANSWERED   # the service asks the model at exactly the threshold


def test_a_refusal_answered_is_a_wrong_answer_and_an_error_is_a_wrong_refusal():
    rates = metrics.rates_at([Outcome("refuse", ANSWERED, 0.5), Outcome("refuse", REFUSED, 0.5),
                              Outcome("answer", ERROR, 0.5), Outcome("answer", ANSWERED, 0.5)], 0.0)
    assert (rates.false_answers, rates.to_refuse, rates.false_refusals, rates.to_answer) == (1, 2, 1, 2)


def test_an_error_on_a_question_to_refuse_is_not_a_wrong_answer():
    assert metrics.rates_at([Outcome("refuse", ERROR, 0.9)], 0.0).false_answers == 0


def test_the_cost_weighs_rates_with_a_wrong_answer_counting_more():
    rates = metrics.Rates(0.3, to_refuse=4, false_answers=1, to_answer=10, false_refusals=5)
    assert rates.cost(2) == pytest.approx(2 * 0.25 + 0.5)


def test_the_threshold_is_the_middle_of_the_widest_run_of_lowest_cost():
    outcomes = [Outcome("refuse", ANSWERED, 0.30), Outcome("answer", ANSWERED, 0.50)]
    sweep = metrics.sweep(outcomes, metrics.threshold_grid(0.0, 0.7, 0.05))
    # below 0.30 the refusal is answered; above 0.50 the answer is refused; 0.35 to 0.50 costs nothing
    assert metrics.choose_threshold(sweep, 2) == pytest.approx(0.45)


def test_a_wrong_answer_costing_more_moves_the_choice_up():
    outcomes = [Outcome("refuse", ANSWERED, 0.40), Outcome("answer", ANSWERED, 0.35),
                Outcome("answer", ANSWERED, 0.60)]
    sweep = metrics.sweep(outcomes, metrics.threshold_grid(0.0, 0.7, 0.05))
    # answering the refusal costs 2 x 1; refusing the 0.35 answer costs 1/2: worth it at 2:1, not at 0.25:1
    assert 0.40 < metrics.choose_threshold(sweep, 2) <= 0.60
    assert metrics.choose_threshold(sweep, 0.25) <= 0.35


def test_the_grid_has_no_floating_point_drift():
    grid = metrics.threshold_grid()
    assert grid[0] == 0.0 and grid[-1] == 0.7 and len(grid) == 71
    assert 0.35 in grid


# ---- latency and sampling


def test_a_percentile_is_a_value_that_was_measured():
    values = [10, 20, 30, 40]
    assert metrics.percentile(values, 50) == 20
    assert metrics.percentile(values, 95) == 40
    assert metrics.percentile([7], 95) == 7


def test_a_sample_does_not_depend_on_the_order_items_come_in():
    items = list(range(40))
    assert metrics.sample(items, 5, "seed") == metrics.sample(list(reversed(items)), 5, "seed")
    assert len(metrics.sample(items, 50, "seed")) == 40
