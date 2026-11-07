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


# ---- Text-to-SQL

GOLD = (("Electronics Black Friday Live", "2025-11-16", "2025-12-12", "1043330.18"),)


def test_a_query_that_returns_only_the_number_asked_for_is_right():
    assert metrics.rows_match(GOLD, (("1043330.18",),))
    assert not metrics.rows_match(GOLD, (("1043330.18",),), every_value=True)


def test_extra_columns_and_another_column_order_are_fine():
    assert metrics.rows_match(GOLD, (("1043330.18", "Electronics Black Friday Live", "15568"),))


def test_the_number_of_another_campaign_is_wrong():
    assert not metrics.rows_match(GOLD, (("987654.32",),))


def test_rounding_inside_half_a_percent_is_the_same_number_and_beyond_it_is_not():
    roi = (("1.1434101017208975",),)
    assert metrics.rows_match(roi, (("1.14",),))
    assert metrics.rows_match((("0.08736",),), (("0.0874",),))
    assert not metrics.rows_match((("0.08736",),), (("0.09",),))


def test_a_row_without_numbers_must_have_all_its_values():
    gold = (("Home New Product Launch", "2026-03-01", "2026-03-17"),)
    assert metrics.rows_match(gold, (("2026-03-17", "Home New Product Launch", "2026-03-01"),))
    assert not metrics.rows_match(gold, (("Home New Product Launch",),))


def test_every_row_must_be_there_once_whatever_the_order():
    gold = (("Serum", "796"), ("Sheet Mask", "470"))
    assert metrics.rows_match(gold, (("470",), ("796",)))
    assert not metrics.rows_match(gold, (("796",), ("796",)))                  # one row can't answer both
    assert not metrics.rows_match((("796",), ("796",)), (("796",), ("470",)))
    assert not metrics.rows_match(gold, (("796",), ("470",), ("12",)))        # a row too many


def test_a_metric_with_two_definitions_needs_both():
    gold = (("8.09", "8.23"),)
    assert metrics.rows_match(gold, (("8.23", "8.09"),))
    assert not metrics.rows_match(gold, (("8.09",),))


def test_the_same_value_twice_in_a_row_must_be_there_twice():
    assert not metrics.rows_match((("5", "5"),), (("5",),))


def test_an_empty_result_matches_only_an_empty_gold():
    assert not metrics.rows_match(GOLD, ())
    assert metrics.rows_match((), ())


def test_nulls_match_only_nulls_and_text_is_compared_exactly():
    assert metrics.rows_match(((None, "Beauty"),), ((None, "Beauty"),))
    assert not metrics.rows_match((("Beauty",),), (("beauty",),))


def test_value_recall_is_the_share_of_the_gold_numbers_found_anywhere():
    gold = (("Grocery Black Friday Live", "28866.58", "34027.23"), ("Grocery Black Friday Live", "181056.65", "1960"))
    assert metrics.value_recall(gold, (("181056.65", "x"), ("28866.58",))) == pytest.approx(0.5)
    assert metrics.value_recall((("Beauty",),), (("Beauty",),)) is None


def test_a_query_names_the_department_by_name_or_by_its_id():
    assert metrics.mentions_department("SELECT 1 FROM campaigns c JOIN departments d USING (department_id) "
                                       "WHERE d.name = 'Beauty'", "Beauty")
    assert metrics.mentions_department("SELECT 1 FROM campaigns WHERE department_id = 3", "Beauty", 3)
    assert not metrics.mentions_department("SELECT 1 FROM campaigns WHERE department_id = 31", "Beauty", 3)
    assert not metrics.mentions_department("SELECT 1 FROM campaigns WHERE name = 'Beauty Black Friday Live'", "Beauty")
