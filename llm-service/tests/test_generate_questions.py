"""The question generator without a model: which tasks the corpus gives, and which answers of the model are kept.
SQL runs in a throwaway database."""
from collections import Counter
from datetime import date
from pathlib import Path

import psycopg
import pytest
import yaml
from postgres_helpers import postgres_available, throwaway_database

from app import db
from evaluation.dataset import Asker, DocumentRef, Section, split_of
from scripts import generate_questions as gq
from scripts.corpus_spec import CAMPAIGNS, EXISTING_SOPS, SOPS
from scripts.generate_mock_data import DEPARTMENTS

REPO_ROOT = Path(__file__).resolve().parents[2]
TEXT = " ".join(["The procedure says what to do and who does it."] * 8)


def title_of(filename: str) -> str:
    return yaml.safe_load((REPO_ROOT / "sop_docs" / filename).read_text(encoding="utf-8").split("---\n", 2)[1])["title"]


def chunks_of(ref: DocumentRef, title: str) -> list[gq.Chunk]:
    return [gq.Chunk(Section(document=ref, heading_path=(title, heading)), f"doc {title}", f"{title} {heading}. {TEXT}")
            for heading in ("When to Use", "Procedure")]


def corpus() -> list[gq.Chunk]:
    """The shape of the real corpus, from its plan, with placeholder text."""
    chunks = []
    for spec in SOPS:
        chunks += chunks_of(DocumentRef(doc_type="sop", department=spec.department, title=spec.title), spec.title)
    for filename, department in EXISTING_SOPS.items():
        title = title_of(filename)
        chunks += chunks_of(DocumentRef(doc_type="sop", department=department, title=title), title)
    for spec in CAMPAIGNS:
        for kind, wanted in (("campaign_plan", spec.plan), ("campaign_review", spec.review)):
            if wanted:
                chunks += chunks_of(DocumentRef(doc_type=kind, department=spec.department, campaign=spec.name,
                                                campaign_start=spec.start), spec.name)
    return chunks


PEOPLE = {"executive": Asker(department=None, role="executive")} | {
    f"{role}:{d}": Asker(department=d, role=role) for d in DEPARTMENTS for role in ("staff", "manager")}


@pytest.fixture(scope="module")
def tasks():
    return gq.plan_tasks(corpus(), PEOPLE)


# ---------------------------------------------------------------------------
# The tasks
# ---------------------------------------------------------------------------
def test_the_plan_asks_for_about_250_questions_of_every_kind(tasks):
    assert Counter(t.kind for t in tasks) == {"fact": 50, "both": 13, "data": 11, "cross_document": 10, "multi_turn": 8,
                                              "near_miss": 5, "unauthorized": 5, "out_of_scope": 2}
    assert sum(t.count for t in tasks) == 257


def test_the_same_corpus_gives_the_same_tasks(tasks):
    assert [t.id for t in gq.plan_tasks(corpus(), PEOPLE)] == [t.id for t in tasks]


def test_every_task_is_its_own_group(tasks):
    assert len({t.id for t in tasks}) == len(tasks)


def test_an_unauthorized_task_is_about_a_document_its_asker_can_not_see(tasks):
    for task in (t for t in tasks if t.kind == "unauthorized"):
        assert not gq.visible(task.asker, task.chunks[0].section.document.department), task.id


def test_a_near_miss_is_about_a_document_its_asker_can_see(tasks):
    for task in (t for t in tasks if t.kind == "near_miss"):
        assert gq.visible(task.asker, task.chunks[0].section.document.department), task.id


def test_a_facts_two_questions_are_asked_in_two_styles_and_every_style_is_used(tasks):
    facts = [t for t in tasks if t.kind == "fact"]
    assert all(len(set(t.styles)) == 2 for t in facts)
    assert set(s for t in facts for s in t.styles) == set(gq.FACT_STYLES)


def test_the_pilot_has_one_task_of_every_kind(tasks):
    assert [t.kind for t in gq.pilot(tasks)] == list(gq.KINDS)


def test_a_cross_document_task_shows_two_documents(tasks):
    for task in (t for t in tasks if t.kind == "cross_document"):
        assert len({c.section.document for c in task.chunks}) == 2, task.id


def test_the_prompt_tells_the_styles_and_keeps_staff_to_their_department(tasks):
    fact = next(t for t in tasks if t.kind == "fact")
    assert f"question 1 in the {fact.styles[0]} style" in gq.task_prompt(None, fact)
    assert gq.department_filter(PEOPLE["staff:Beauty"]) == " (filter on departments.name = 'Beauty')"
    assert gq.department_filter(PEOPLE["executive"]) == ""


# ---------------------------------------------------------------------------
# What is kept of the model's answer
# ---------------------------------------------------------------------------
REF = DocumentRef(doc_type="sop", department="Fresh Grocery", title="Host Onboarding and Review (Fresh Grocery)")
CHUNK = gq.Chunk(Section(document=REF, heading_path=("Host Onboarding and Review (Fresh Grocery)", "Steps")), "doc",
                 "Probation lasts 45 days. A new host does 2 trial streams.")


def item(**overrides):
    return {"question": "How long is probation for a new host?", "style": "direct", "answer_points": ["45 days"],
            "sections": ["S1"], "sql": None, "previous_question": None, "previous_answer": None, **overrides}


def task(kind="fact", asker=PEOPLE["staff:Fresh Grocery"], theme=""):
    return gq.Task(f"{kind}:test", kind, (CHUNK,), asker, 1, theme=theme)


def build(t, i):
    return gq.build(None, t, i, {"S1": CHUNK}, 0)


def test_a_fact_is_kept_with_its_section_route_and_split():
    question = build(task(), item())
    assert (question.route, question.expected, question.tags) == ("documents", "answer", ("direct",))
    assert question.relevant == (CHUNK.section,)
    assert question.split == split_of("fact:test") and question.group == "fact:test"


def test_an_answer_point_with_a_number_its_section_does_not_have_is_dropped():
    with pytest.raises(ValueError, match="numbers not in its sections"):
        build(task(), item(answer_points=["60 days"]))


def test_a_question_that_names_a_section_it_was_not_shown_is_dropped():
    with pytest.raises(ValueError, match="wasn't shown"):
        build(task(), item(sections=["S7"]))


def test_an_unauthorized_question_must_refuse_and_name_what_it_must_not_get():
    question = build(task("unauthorized", asker=PEOPLE["staff:Beauty"]), item())
    assert (question.expected, question.hidden, question.relevant) == ("refuse", (CHUNK.section,), ())
    assert "unauthorized" in question.tags and question.answer_points == ()


def test_an_unauthorized_question_its_asker_could_answer_is_dropped():
    with pytest.raises(ValueError, match="can see"):
        build(task("unauthorized", asker=PEOPLE["staff:Fresh Grocery"]), item())


def test_a_near_miss_refuses_and_keeps_what_it_is_close_to():
    question = build(task("near_miss"), item(sections=[], answer_points=[]))
    assert (question.expected, question.related, question.relevant) == ("refuse", (CHUNK.section,), ())


def test_a_follow_up_without_its_first_turn_is_dropped():
    with pytest.raises(ValueError, match="first turn"):
        build(task("multi_turn"), item())
    question = build(task("multi_turn"), item(previous_question="Who runs trial streams?", previous_answer="New hosts."))
    assert [t.role for t in question.turns] == ["user", "assistant"] and "multi_turn" in question.tags


def test_a_question_about_numbers_needs_sql_that_keeps_to_the_askers_department():
    with pytest.raises(ValueError, match="no SQL"):
        build(task("data", theme="single_table"), item(sections=[]))
    with pytest.raises(ValueError, match="Fresh Grocery"):
        build(task("data", theme="single_table"), item(sections=[], sql="SELECT 1"))


# ---------------------------------------------------------------------------
# SQL, in a throwaway database
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def database():
    if not postgres_available():
        pytest.skip("needs the docker compose Postgres")
    with throwaway_database() as name:
        with db.connect(name) as conn:
            conn.execute("INSERT INTO departments (name) VALUES ('Beauty'), ('Fresh Grocery')")
            conn.execute("INSERT INTO hosts (name, department_id) VALUES ('Tommy Walter', 1), ('Jeffrey Chavez', 2)")
            conn.execute("""INSERT INTO campaigns (name, department_id, host_id, start_date, end_date, status) VALUES
                            ('Beauty Summer Sale', 1, 1, '2026-06-12', '2026-07-21', 'finished'),
                            ('Grocery Summer Sale', 2, 2, '2026-06-09', '2026-07-18', 'finished')""")
            conn.execute("INSERT INTO campaign_metrics (campaign_id, date, gmv) VALUES (1, '2026-06-12', 1000.50)")
        yield name


def test_sql_results_are_kept_as_exact_text(database):
    with db.connect(database) as conn:
        assert gq.run_sql(conn, "SELECT sum(gmv), count(*) FROM campaign_metrics;") == (("1000.50", "1"),)


@pytest.mark.parametrize("sql, error", [
    ("DELETE FROM campaigns", "single SELECT"),
    ("SELECT 1; SELECT 2", "single SELECT"),
    ("SELECT name FROM campaigns WHERE false", "no rows"),
])
def test_sql_that_is_not_one_select_with_rows_is_dropped(database, sql, error):
    with db.connect(database) as conn, pytest.raises(ValueError, match=error):
        gq.run_sql(conn, sql)


def test_sql_can_not_write_even_from_inside_a_select(database):
    with db.connect(database) as conn:
        with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
            gq.run_sql(conn, "WITH gone AS (DELETE FROM campaigns RETURNING 1) SELECT * FROM gone")
        assert conn.execute("SELECT count(*) FROM campaigns").fetchone()[0] == 2


def test_sql_that_runs_too_long_is_stopped(database, monkeypatch):
    monkeypatch.setattr(gq, "SQL_TIMEOUT_MS", 100)
    with db.connect(database) as conn, pytest.raises(psycopg.errors.QueryCanceled):
        gq.run_sql(conn, "SELECT pg_sleep(2)")


def test_a_question_about_numbers_keeps_its_sql_and_result(database):
    with db.connect(database) as conn:
        question = gq.build(conn, task("data", asker=PEOPLE["staff:Beauty"], theme="single_table"),
                            item(sections=[], sql="""SELECT c.name FROM campaigns c JOIN departments d USING (department_id)
                                                    WHERE d.name = 'Beauty'"""), {}, 0)
    assert (question.route, question.gold_result, question.relevant) == ("data", (("Beauty Summer Sale",),), ())
    assert set(question.tags) == {"direct", "single_table"}


def test_the_schema_shows_only_the_askers_campaigns_and_no_ip_addresses(database):
    with db.connect(database) as conn:
        text = gq.schema_text(conn, PEOPLE["staff:Beauty"])
    assert "Beauty Summer Sale" in text and "Grocery Summer Sale" not in text
    assert "ip_address" not in text


def test_a_follow_up_is_a_duplicate_only_after_the_same_conversation():
    first = build(task("multi_turn"), item(previous_question="Who runs trial streams?", previous_answer="New hosts."))
    other = build(task("multi_turn"), item(previous_question="Who signs off?", previous_answer="The manager."))
    assert first.question == other.question
    assert gq.dedupe_key(first) != gq.dedupe_key(other)


LAUNCH_STARTS = (date(2026, 1, 8), date(2026, 1, 18), date(2026, 1, 24), date(2026, 3, 1))


def test_a_task_knows_which_of_its_campaign_names_repeat_and_when_a_month_is_not_enough():
    tasks = gq.plan_tasks(corpus(), PEOPLE, {"Home New Product Launch": LAUNCH_STARTS, "Beauty Summer Sale": (date(2026, 6, 12),)})
    launches = [t for t in tasks if any(c.section.document.campaign == "Home New Product Launch" for c in t.chunks)]
    assert launches and all(t.repeated == (("Home New Product Launch", LAUNCH_STARTS),) for t in launches)
    assert all(t.need_day and set(t.need_day) <= set(LAUNCH_STARTS[:3]) for t in launches)
    assert all(t.repeated == () and t.need_day == () for t in tasks if t not in launches)


def test_the_prompt_says_which_campaign_names_repeat():
    prompt = gq.task_prompt(None, gq.Task("fact:x", "fact", (CHUNK,), PEOPLE["staff:Fresh Grocery"], 1, styles=("direct",),
                                          repeated=(("Home New Product Launch", LAUNCH_STARTS[:2]),)))
    assert '"Home New Product Launch" (starting Jan 8, 2026, Jan 18, 2026)' in prompt


def test_a_question_about_a_campaign_whose_name_repeats_must_say_which():
    launch = gq.Task("fact:launch", "fact", (CHUNK,), PEOPLE["staff:Fresh Grocery"], 1,
                     repeated=(("Home New Product Launch", (date(2026, 1, 8), date(2026, 3, 1))),))
    with pytest.raises(ValueError, match="which Home New Product Launch"):
        build(launch, item(question="How long is probation after the launch?"))
    assert build(launch, item(question="How long is probation after the January launch?")).question


@pytest.mark.parametrize("asked", ["the Jan 18 launch", "the January 18 launch", "the launch starting 2026-01-18",
                                   "the launch on the 18th", "the 1/18 launch"])
def test_when_two_start_in_the_same_month_the_day_tells_them_apart(asked):
    review = gq.Chunk(Section(document=DocumentRef(doc_type="campaign_review", department="Home Goods",
                                                   campaign="Home New Product Launch", campaign_start=date(2026, 1, 18)),
                              heading_path=("Home New Product Launch (Jan 18, 2026) - Post-Campaign Review", "Results")),
                      "doc", "Spin Mop led profit.")
    launch = gq.Task("fact:launch", "fact", (review,), PEOPLE["staff:Home Goods"], 1,
                     repeated=(("Home New Product Launch", LAUNCH_STARTS),), need_day=(date(2026, 1, 18),))
    point = dict(answer_points=["Spin Mop"])
    with pytest.raises(ValueError, match="give the day"):
        gq.build(None, launch, item(question="What led profit in the January launch?", **point), {"S1": review}, 0)
    assert gq.build(None, launch, item(question=f"What led profit in {asked}?", **point), {"S1": review}, 0).question
