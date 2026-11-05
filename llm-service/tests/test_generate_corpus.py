"""The corpus generator without a model: how numbers are written, the rules a draft must keep, the plan of the corpus,
and the facts it reads from the database (a throwaway one)."""
import unicodedata
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from postgres_helpers import postgres_available, throwaway_database

from app import db
from scripts import generate_corpus as g
from scripts.corpus_spec import (ALL, CAMPAIGNS, EXISTING_SOPS, PILOT, PLAN_AUTHORS, REVIEW_AUTHORS, SOP_AUTHORS, SOPS,
                                 TEMPLATE_SECTIONS, CampaignSpec, Section)
from scripts.generate_mock_data import DEPARTMENTS

REPO_ROOT = Path(__file__).resolve().parents[2]
SAMPLE_REVIEW = (REPO_ROOT / "campaign_docs" / "beauty-black-friday-live-review.md").read_text(encoding="utf-8")

# Campaign 1 of the seeded mock data, the campaign the hand-written sample review is about
BLACK_FRIDAY = g.CampaignFacts(
    campaign_id=1, name="Beauty Black Friday Live", department="Beauty", host="Tommy Walter",
    start=date(2025, 11, 18), end=date(2025, 12, 14), status="finished", live_days=27,
    planned_cost=Decimal("71061.53"), actual_cost=Decimal("73552.32"), gmv=Decimal("600441.45"),
    conversion=Decimal("0.08617037037037037037"), peak_viewers=6488,
    products=(g.Product("Sheet Mask", 470, Decimal("131623.43"), Decimal("45439.53"), Decimal("0.3452"), Decimal("0.1023")),
              g.Product("Lipstick", 528, Decimal("127634.20"), Decimal("59168.44"), Decimal("0.4636"), Decimal("0.1919")),
              g.Product("Serum", 796, Decimal("110119.84"), Decimal("64158.80"), Decimal("0.5826"), Decimal("0.1972"))))
TARGETS = g.Targets(g.Range(Decimal(500_000), Decimal(700_000)), g.Range(Decimal("0.06"), Decimal("0.08")),
                    g.Range(Decimal(5000), Decimal(7000)))


def review_text() -> str:
    lines, blocks = g.review_parts(BLACK_FRIDAY, TARGETS, has_plan=True)
    return "\n".join(lines + list(blocks.values()))


# ---------------------------------------------------------------------------
# Numbers
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("written", ["$600.4K", "8.62%", "6,488", "$71.1K", "$73.6K", "2.29", "34.52%", "10.2%"])
def test_numbers_are_written_the_way_the_hand_written_review_writes_them(written):
    assert written in review_text()
    assert written in SAMPLE_REVIEW


def test_the_product_table_has_one_row_per_product_in_the_samples_format():
    assert "| Sheet Mask | 470 | $131.6K | $45.4K | 34.52% | 10.2% |" in review_text()


def test_roi_is_the_product_profit_over_the_actual_operating_cost():
    assert "168.8 / 73.6 ≈ 2.29" in review_text()


@pytest.mark.parametrize("actual, verdict", [(Decimal(499_999), "Below target"), (Decimal(500_000), "On target"),
                                             (Decimal(700_000), "On target"), (Decimal(700_001), "Above target")])
def test_a_result_on_the_edge_of_its_range_is_on_target(actual, verdict):
    assert TARGETS.gmv.verdict(actual) == verdict


def test_spending_more_than_5_percent_over_the_plan_is_flagged():
    assert g.cost_verdict(Decimal(100), Decimal(105)) == "5.0% over budget"
    assert g.cost_verdict(Decimal(100), Decimal(106)).startswith("**6.0% over budget**, flagged")
    assert g.cost_verdict(Decimal(100), Decimal(90)) == "10.0% under budget"


def test_a_campaigns_targets_are_the_same_every_time_and_differ_between_campaigns():
    other = g.CampaignFacts(**{**BLACK_FRIDAY.__dict__, "campaign_id": 2})
    assert g.targets_for(BLACK_FRIDAY) == g.targets_for(BLACK_FRIDAY)
    assert g.targets_for(BLACK_FRIDAY) != g.targets_for(other)


def test_a_running_campaign_is_judged_by_its_pace_so_far():
    running = g.CampaignFacts(**{**BLACK_FRIDAY.__dict__, "status": "running", "live_days": 9})   # a third of 27 days
    finished_gmv = g.targets_for(BLACK_FRIDAY).gmv
    assert g.targets_for(running).gmv.low > finished_gmv.high   # three times the pace, so far higher targets


# ---------------------------------------------------------------------------
# The rules a draft must keep
# ---------------------------------------------------------------------------
def test_a_number_that_is_not_in_the_facts_is_caught():
    assert g.stray_numbers("GMV reached $650K, up 12%.", review_text()) == ["12%", "650K"]


def test_a_number_from_the_facts_passes_however_its_thousands_are_written():
    assert g.stray_numbers("GMV was $600.4K or 600.4K, with 6488 viewers at the peak.", review_text()) == []


def test_rounding_a_number_differently_counts_as_a_new_number():
    assert g.stray_numbers("GMV was about $600K.", review_text()) == ["600K"]


def test_dates_years_and_counting_words_are_not_facts():
    assert g.stray_numbers("From Nov 18, 2025 to December 14, 2025 we featured 3 products over 2 segments in 2025.", "") == []


def test_a_number_inside_a_word_or_a_code_is_not_a_number():
    assert g.stray_numbers("Stock from warehouse B12 and the Q4 plan.", "") == []


def check(draft, **overrides):
    rules = dict(title="Refund Rules", sections=(Section("When to Use", ""), Section("Procedure", "")), blocks={},
                 allowed_text="Within 30 days", forbidden_names=["Home Goods"], words=(5, 100))
    return g.check_draft(draft, **{**rules, **overrides})


GOOD = "# Refund Rules\n\n## When to Use\n\nAlways.\n\n## Procedure\n\n1. Return it within 30 days.\n"


def test_a_draft_that_keeps_every_rule_has_no_problems():
    assert check(GOOD) == []


def test_a_draft_with_another_title_is_rejected():
    assert any("first line" in p for p in check(GOOD.replace("# Refund Rules", "# Refunds")))


def test_a_draft_with_a_heading_of_its_own_is_rejected():
    assert any("sections" in p for p in check(GOOD + "\n## Notes\n\nNone.\n"))


TABLE = {"{{TABLE}}": "| Product | Units |\n|---|---|\n| Serum | 30 |"}


def test_a_draft_that_drops_or_repeats_a_placeholder_is_rejected():
    assert any("{{TABLE}}" in p for p in check(GOOD, blocks=TABLE))
    assert any("{{TABLE}}" in p for p in check(GOOD + "{{TABLE}}\n{{TABLE}}\n", blocks=TABLE))
    assert check(GOOD + "{{TABLE}}\n", blocks=TABLE) == []


def test_a_draft_that_writes_out_a_table_as_well_as_its_placeholder_is_rejected():
    copied = GOOD + "{{TABLE}}\n\n| Product | Units |\n|---|---|\n| Serum | 30 |\n"
    assert any("table" in p for p in check(copied, blocks=TABLE, allowed_text="30 Within 30 days"))


def test_a_draft_that_repeats_a_line_of_a_list_block_is_rejected():
    blocks = {"{{TARGETS}}": "- Expected GMV: $500,000-$700,000"}
    draft = GOOD + "{{TARGETS}}\n- Expected GMV: $500,000-$700,000\n"
    assert any("block" in p for p in check(draft, blocks=blocks, allowed_text="$500,000 $700,000 30 days"))


def test_a_department_document_that_names_another_department_is_rejected():
    assert any("Home Goods" in p for p in check(GOOD + "Ask Home Goods.\n"))


def test_a_draft_of_the_wrong_length_is_rejected():
    assert any("words" in p for p in check(GOOD, words=(500, 900)))


def test_the_tables_go_where_the_placeholders_were_and_the_frontmatter_comes_first():
    job = g.Job(path=Path("x.md"), title="T", author="standard", frontmatter={"title": "T", "department": "Beauty"},
                prompt="", sections=(), blocks={"{{TABLE}}": "| a |"}, allowed_text="", forbidden_names=[], words=(0, 99),
                mixed_paragraphs=False)
    assert g.render(job, "# T\n\n{{TABLE}}\n") == "---\ntitle: T\ndepartment: Beauty\n---\n\n# T\n\n| a |\n"


def test_headings_and_tables_get_one_blank_line_around_them():
    job = g.Job(path=Path("x.md"), title="T", author="standard", frontmatter={"title": "T"}, prompt="", sections=(),
                blocks={"{{TABLE}}": "| a |"}, allowed_text="", forbidden_names=[], words=(0, 99), mixed_paragraphs=False)
    assert g.render(job, "# T\n## A\n{{TABLE}}\nText.\n\n\n\n## B\nMore.") == (
        "---\ntitle: T\n---\n\n# T\n\n## A\n\n| a |\n\nText.\n\n## B\n\nMore.\n")


def test_a_plan_names_its_products_and_promotions_but_no_prices():
    lines, _ = g.plan_parts(BLACK_FRIDAY, g.planned_lineup(BLACK_FRIDAY), TARGETS)
    products = [line for line in lines if line.startswith("  - ")]
    assert [line.split(":")[0].strip(" -") for line in products] == ["Sheet Mask", "Lipstick", "Serum"]
    assert "$280" not in "\n".join(lines)   # the average price of a sheet mask in the mock data


def test_a_plans_budget_is_rounded_to_the_nearest_thousand():
    nearly_29 = g.CampaignFacts(**{**BLACK_FRIDAY.__dict__, "planned_cost": Decimal("28910.00")})
    lines, _ = g.plan_parts(nearly_29, g.planned_lineup(nearly_29), TARGETS)
    assert "Planned operating cost: about $29,000" in lines


@pytest.mark.parametrize("example, placeholders", [(g.example_plan, ["{{TARGETS}}"]),
                                                   (g.example_review, ["{{RESULTS_TABLE}}", "{{ROI_BLOCK}}", "{{PRODUCT_TABLE}}"])])
def test_the_example_a_model_sees_has_placeholders_instead_of_tables(example, placeholders):
    text = example()
    assert all(text.count(p) == 1 for p in placeholders)
    assert not any(line.startswith("|") for line in text.splitlines())
    assert "formula changed" not in text


def test_a_repeated_campaign_name_is_told_apart_by_its_start_date():
    spec = CampaignSpec("Home Goods", "Home New Product Launch", date(2026, 1, 8), plan=True, review=True)
    assert g.campaign_title(spec, "plan", {"Home New Product Launch"}) == "Home New Product Launch (Jan 8, 2026) - Campaign Plan"
    assert g.campaign_title(spec, "review", set()) == "Home New Product Launch - Post-Campaign Review"


# ---------------------------------------------------------------------------
# The plan of the corpus
# ---------------------------------------------------------------------------
def departments_of_all_sops() -> list[str]:
    return [s.department for s in SOPS] + list(EXISTING_SOPS.values())


def test_there_are_30_sops_10_of_them_company_wide_and_5_for_each_department():
    departments = departments_of_all_sops()
    assert len(departments) == 30
    assert departments.count(ALL) == 10
    assert all(departments.count(d) == 5 for d in DEPARTMENTS)


def test_every_sop_names_a_real_department_and_a_new_file():
    assert {s.department for s in SOPS} <= set(DEPARTMENTS) | {ALL}
    filenames = [s.filename for s in SOPS]
    assert len(set(filenames)) == len(filenames)
    assert not set(filenames) & set(EXISTING_SOPS)


def test_no_two_sops_of_a_department_share_a_title():
    # Compared the way the database compares SOP titles (uq_documents_sop_title)
    keys = [(s.department, unicodedata.normalize("NFKC", s.title).strip().casefold()) for s in SOPS]
    assert len(set(keys)) == len(keys)


def test_each_sop_that_copies_a_structure_copies_an_existing_one_of_another_department():
    for spec in SOPS:
        if spec.like:
            assert spec.like in EXISTING_SOPS
            assert EXISTING_SOPS[spec.like] != spec.department


def test_each_structure_is_shared_by_all_four_departments():
    for like, department in {s.like: None for s in SOPS if s.like}.items():
        departments = {s.department for s in SOPS if s.like == like} | {EXISTING_SOPS[like]}
        assert departments == set(DEPARTMENTS)


def test_30_campaigns_with_some_missing_one_of_their_documents():
    keys = [(c.department, c.name, c.start) for c in CAMPAIGNS]
    assert len(set(keys)) == 30
    assert sum(not c.review for c in CAMPAIGNS) == 2
    assert sum(not c.plan for c in CAMPAIGNS) == 3


# ---------------------------------------------------------------------------
# Authors
# ---------------------------------------------------------------------------
LONG = " ".join(["word"] * 110)


def test_a_paragraph_is_prose_only():
    text = ("# Title\n\nA short one.\n\n- a list item\n- another\n\n1. a step\n\n| a | table |\n\n> a quote\n\n"
            "{{TABLE}}\n\n" + LONG)
    assert g.paragraph_lengths(text) == [3, 110]


def test_a_paragraph_that_starts_in_bold_is_prose_not_a_list():
    assert g.paragraph_lengths("**Why.** " + LONG + "\n\n- an item") == [111]   # "Why" and the 110 words of LONG


def test_an_author_who_mixes_paragraph_lengths_needs_a_long_paragraph_and_a_short_piece():
    loose = dict(mixed_paragraphs=True, words=(0, 999))
    nothing_short = f"# Refund Rules\n\n## When to Use\n\n{LONG}\n\n## Procedure\n\n1. {' '.join(['step'] * 30)}\n"
    assert any("paragraph" in p for p in check(GOOD, **loose))                     # no long paragraph
    assert any("paragraph" in p for p in check(nothing_short, **loose))            # a long paragraph, a long step
    assert check(nothing_short.replace(LONG, LONG + "\n\nShort."), **loose) == []  # a short paragraph
    assert check(GOOD.replace("Always.", LONG), **loose) == []                     # a one-line step is short too
    long_step_only = GOOD.replace("Return it within 30 days.", LONG)
    assert any("paragraph" in p for p in check(long_step_only, **loose))           # a long step isn't a long paragraph


def test_a_draft_with_a_number_outside_its_facts_is_rejected():
    assert any("45" in p for p in check(GOOD + "\nOr within 45 days for members.\n"))


def test_the_paragraph_rule_tells_the_model_what_it_counted():
    problems = check(GOOD.replace("Always.", "Five words in this paragraph."), mixed_paragraphs=True, words=(0, 999))
    assert any("5 words" in p and "lists" in p for p in problems)


def test_a_placeholder_under_another_section_is_rejected():
    sections = (Section("When to Use", ""), Section("Procedure", "", ("{{TABLE}}",)))
    misplaced = GOOD.replace("Always.", "Always.\n\n{{TABLE}}")
    assert any("belongs under ## Procedure" in p for p in check(misplaced, sections=sections, blocks=TABLE))
    assert check(GOOD + "{{TABLE}}\n", sections=sections, blocks=TABLE) == []


def test_an_sop_may_have_a_table_of_its_own():
    assert check(GOOD + "\n| Level | Who |\n|---|---|\n| one | an agent |\n") == []


def test_each_document_keeps_its_author():
    assert [g.sop_author(spec).name for spec in SOPS] == [g.sop_author(spec).name for spec in SOPS]


def test_a_departmental_twin_keeps_the_template_sections_whoever_writes_it():
    for spec in SOPS:
        if spec.like:
            assert g.sop_sections(spec, g.sop_author(spec)) == TEMPLATE_SECTIONS


def test_every_sop_style_writes_at_least_3_documents():
    styles = [g.sop_author(spec).name for spec in SOPS] + ["standard"] * len(EXISTING_SOPS)
    assert [name for name in SOP_AUTHORS if styles.count(name) < 3] == []


def test_every_plan_and_review_style_writes_at_least_3_documents():
    for kind, authors in (("plan", PLAN_AUTHORS), ("review", REVIEW_AUTHORS)):
        styles = [g.campaign_author(c, kind).name for c in CAMPAIGNS if getattr(c, kind)]
        assert [name for name in authors if styles.count(name) < 3] == [], kind


@pytest.mark.parametrize("authors, placeholders", [(PLAN_AUTHORS, ["{{TARGETS}}"]),
                                                   (REVIEW_AUTHORS, ["{{RESULTS_TABLE}}", "{{ROI_BLOCK}}", "{{PRODUCT_TABLE}}"])])
def test_every_campaign_author_places_each_block_exactly_once(authors, placeholders):
    for author in authors.values():
        assert sorted(p for s in author.sections for p in s.placeholders) == sorted(placeholders), author.name


def test_the_pilot_shows_every_author_but_the_one_the_samples_already_show():
    names = {spec.filename: g.sop_author(spec).name for spec in SOPS}
    names |= {g.campaign_filename(c, k): f"{k}-{g.campaign_author(c, k).name}" for c in CAMPAIGNS for k in ("plan", "review")}
    assert {names[f] for f in PILOT} == {"terse", "faq", "explainer", "plan-memo", "plan-brief", "review-recap",
                                         "review-story", "review-wins"}


def test_the_pilot_is_part_of_the_corpus():
    planned = {s.filename for s in SOPS} | {g.campaign_filename(c, k) for c in CAMPAIGNS for k in ("plan", "review")}
    assert set(PILOT) <= planned


# ---------------------------------------------------------------------------
# Facts from the database
# ---------------------------------------------------------------------------
@pytest.mark.skipif(not postgres_available(), reason="needs the docker compose Postgres")
def test_the_facts_add_up_the_daily_metrics_and_list_products_by_sales():
    with throwaway_database() as name, db.connect(name) as conn:
        conn.execute("INSERT INTO departments (name) VALUES ('Beauty')")
        conn.execute("INSERT INTO hosts (name, department_id) VALUES ('Tommy Walter', 1)")
        conn.execute("""INSERT INTO campaigns (name, department_id, host_id, start_date, end_date, status,
                                               planned_operating_cost, actual_operating_cost)
                        VALUES ('Beauty Black Friday Live', 1, 1, '2025-11-18', '2025-11-19', 'finished', 1000, 800)""")
        conn.execute("""INSERT INTO campaign_metrics (campaign_id, date, gmv, conversion_rate, peak_concurrent_viewers)
                        VALUES (1, '2025-11-18', 1000.50, 0.05, 300), (1, '2025-11-19', 2000.25, 0.07, 500)""")
        conn.execute("""INSERT INTO campaign_products (campaign_id, product_name, units_sold, sales_amount, profit,
                                                       profit_margin, return_rate)
                        VALUES (1, 'Serum', 10, 500, 200, 0.4, 0.1), (1, 'Lipstick', 20, 900, 300, 0.33, 0.2)""")
        facts = g.load_campaign(conn, CampaignSpec("Beauty", "Beauty Black Friday Live", date(2025, 11, 18), True, True))
    assert (facts.gmv, facts.conversion, facts.peak_viewers, facts.live_days) == (Decimal("3000.75"), Decimal("0.06"), 500, 2)
    assert [p.name for p in facts.products] == ["Lipstick", "Serum"]
    assert facts.roi == Decimal("500") / Decimal("800")
