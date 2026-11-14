"""Finding the campaign a question means by its place in time, against a throwaway database as sql_reader: only campaigns
the asker may see, another department's reading the same as none, and asking back when more than one fits."""
from datetime import date

import pytest
from postgres_helpers import login_in, postgres_available, throwaway_database

from app import db
from app.router.rewriting import CampaignReference
from app.sql_agent import campaigns
from app.sql_agent.campaigns import CampaignFinder, Found
from app.time_budget import TimeBudget

needs_postgres = pytest.mark.skipif(not postgres_available(), reason="needs the docker compose Postgres")

# Grocery's latest finished campaign ends after every Beauty one, and so does its Black Friday: "the last one" of a
# Beauty asker must still be Beauty's. Two Beauty spring campaigns end on the same day
CAMPAIGNS = [
    # name, department, start, end, status, has a review
    ("Beauty Black Friday Live", 1, "2024-11-18", "2024-12-14", "finished", True),
    ("Beauty Black Friday Live", 1, "2025-11-18", "2025-12-14", "finished", False),
    ("Beauty Spring Sale", 1, "2026-04-01", "2026-04-30", "finished", True),
    ("Beauty Spring Flash", 1, "2026-04-20", "2026-04-30", "finished", False),
    ("Beauty Summer Sale", 1, "2026-06-12", "2026-07-21", "finished", True),
    ("Beauty Fan Day", 1, "2026-10-10", "2026-10-30", "running", False),
    ("Grocery Black Friday Live", 2, "2025-11-12", "2025-12-20", "finished", True),
    ("Grocery Harvest Sale", 2, "2026-09-01", "2026-09-20", "finished", False),
]


@pytest.fixture(scope="module")
def database():
    if not postgres_available():
        pytest.skip("needs the docker compose Postgres")
    with throwaway_database() as name:
        with db.connect(name) as conn:
            conn.execute("INSERT INTO departments (name) VALUES ('Beauty'), ('Fresh Grocery')")
            conn.execute("INSERT INTO hosts (name, department_id, category) VALUES ('Tommy Walter', 1, 'Beauty'), "
                         "('Ana Ruiz', 2, 'Fresh Grocery')")
            for name_, department, start, end, status, reviewed in CAMPAIGNS:
                conn.execute("""INSERT INTO campaigns (name, department_id, host_id, start_date, end_date, status,
                                                       planned_operating_cost, actual_operating_cost, has_review_doc)
                                VALUES (%s, %s, %s, %s, %s, %s, 10.00, %s, %s)""",
                             (name_, department, department, start, end, status,
                              None if status == "running" else 12.00, reviewed))
        yield name


@pytest.fixture(scope="module")
def reader_login(database):
    with login_in("sql_reader") as login:
        yield login


@pytest.fixture
async def finder(database, reader_login):
    user, password = reader_login
    async with db.query_pool(database) as pool, db.sql_pool(database, user=user, password=password) as reader:
        await pool.wait()
        await reader.wait()
        yield CampaignFinder(pool, reader)


def ref(words=None, position=1, department=None) -> CampaignReference:
    return CampaignReference(words=words, department=department, position=position)


async def find(finder, reference, role="staff", department="Beauty") -> Found:
    return await finder.find(reference, role=role, department=department, budget=TimeBudget(10))


def named(found: Found) -> tuple:
    c = found.campaign
    return (c.name, c.start_date.isoformat()) if c else None


@needs_postgres
@pytest.mark.anyio
async def test_the_last_campaign_is_the_askers_departments_latest_finished_even_when_another_ended_later(finder):
    assert named(await find(finder, ref())) == ("Beauty Summer Sale", "2026-06-12")   # not the running Fan Day
    assert named(await find(finder, ref(), department="Fresh Grocery")) == ("Grocery Harvest Sale", "2026-09-01")


@pytest.mark.anyio
@needs_postgres
async def test_words_narrow_by_name_and_a_position_counts_back_from_the_latest(finder):
    last = await find(finder, ref("black friday"))
    assert named(last) == ("Beauty Black Friday Live", "2025-11-18")
    assert [c.start_date for c in last.earlier] == [date(2024, 11, 18)]
    assert named(await find(finder, ref("Black Friday livestream", position=2))) == ("Beauty Black Friday Live",
                                                                                      "2024-11-18")
    assert await find(finder, ref("black friday", position=3)) == Found(None, (), ())


@needs_postgres
@pytest.mark.anyio
async def test_another_departments_campaign_reads_exactly_as_one_that_doesnt_exist(finder):
    nothing = Found(None, (), ())
    assert await find(finder, ref("harvest")) == nothing
    assert await find(finder, ref("no such campaign")) == nothing
    assert await find(finder, ref(None, department="Fresh Grocery")) == nothing


@needs_postgres
@pytest.mark.anyio
@pytest.mark.parametrize("layer", ["row-level security alone", "the code's filter alone"])
async def test_each_layer_alone_keeps_another_departments_campaigns_out(finder, monkeypatch, layer):
    """The search is bounded twice, as generated SQL is: the department the transaction sets, which row-level security
    reads, and the department in the WHERE clause. Either one alone still finds nothing of Fresh Grocery's."""
    if layer == "row-level security alone":
        monkeypatch.setattr(campaigns, "_IN_DEPARTMENT", "")
    else:
        real = campaigns.run_select

        async def every_row(conn, sql, **kwargs):
            return await real(conn, sql, **(kwargs | {"department": None, "every_department": True}))
        monkeypatch.setattr(campaigns, "run_select", every_row)
    assert named(await find(finder, ref())) == ("Beauty Summer Sale", "2026-06-12")
    assert await find(finder, ref("harvest")) == Found(None, (), ())


@needs_postgres
@pytest.mark.anyio
async def test_an_executive_is_asked_which_department_with_each_departments_own_and_only_those(finder):
    found = await find(finder, ref("black friday"), role="executive", department=None)
    assert found.campaign is None
    assert [(c.department, c.start_date.isoformat()) for c in found.candidates] == [("Fresh Grocery", "2025-11-12"),
                                                                                   ("Beauty", "2025-11-18")]
    assert named(await find(finder, ref("black friday", department="beauty"), role="admin",
                            department=None)) == ("Beauty Black Friday Live", "2025-11-18")
    # Only one department ran a harvest sale: nothing to ask
    assert named(await find(finder, ref("harvest"), role="executive", department=None)) == ("Grocery Harvest Sale",
                                                                                            "2026-09-01")


@needs_postgres
@pytest.mark.anyio
async def test_two_campaigns_that_ended_on_the_same_day_are_asked_back_with_both(finder):
    found = await find(finder, ref("spring"))
    assert found.campaign is None and {c.name for c in found.candidates} == {"Beauty Spring Sale", "Beauty Spring Flash"}


@needs_postgres
@pytest.mark.anyio
async def test_whether_a_campaign_has_a_review_is_read_only_for_campaigns_already_found(finder):
    last = await find(finder, ref("black friday"))
    reviewed = await finder.with_review((last.campaign, *last.earlier), TimeBudget(10))
    assert reviewed == {last.earlier[0].campaign_id}   # the 2025 one has none yet, the 2024 one has
    assert await finder.with_review((), TimeBudget(10)) == set()


def test_words_that_say_what_a_campaign_is_but_not_which_are_dropped():
    assert campaigns.patterns("our last Black Friday livestream campaign") == ["%black%", "%friday%"]
    assert campaigns.patterns("the most recent product launch") == ["%product%", "%launch%"]
    assert campaigns.patterns(None) == ["%"] and campaigns.patterns("the campaign") == ["%"]


def test_an_impossible_asker_is_refused_before_any_search():
    with pytest.raises(ValueError):
        import asyncio
        asyncio.run(CampaignFinder(None, None).find(ref(), role="staff", department=None, budget=TimeBudget(10)))
