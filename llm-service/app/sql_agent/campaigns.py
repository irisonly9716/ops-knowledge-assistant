"""Which campaign a question means when it names one by its place in time, such as "the last Black Friday".

The search is a query written here, run the way a model's query is: as sql_reader on the metric views, under row-level
security, in the asker's department (execute.run_select), with the department in the WHERE clause as well. So it can
only find campaigns the asker may see, and a campaign of another department reads the same as one that doesn't exist:
neither is found. No list of the company's campaigns ever goes to a model.

The rules:
- Staff and managers: their department's finished campaigns, the latest end date first. Words narrow them by name: a
  campaign has every word in its name ("Black Friday" finds "Beauty Black Friday Live").
- Executives and admins see every department. A question that names none, about a kind of campaign more than one
  department ran, is asked back with each department's: every department runs its own Black Friday, and a guess
  would be wrong for most of them.
- Two campaigns at the place asked for that ended on the same day: asked back with both.
- None: a fixed sentence.

Whether a campaign has a post-campaign review is in campaigns.has_review_doc, which no view has and sql_reader can't read.
It is read as ops, by id, and only for campaigns this search has already found, so it says nothing about any other.
"""
import re
from dataclasses import dataclass
from datetime import date
from typing import Any

from app.router.rewriting import CampaignReference
from app.sql_agent.execute import STATEMENT_TIMEOUT_MS, run_select
from app.sql_agent.schema import visible_department
from app.time_budget import TimeBudget

NOT_FOUND_TEXT = "I couldn't find a finished campaign you have access to that matches."
EACH_DEPARTMENT_TEXT = "Each department ran its own: which one do you mean? Ask again with its name."
SAME_DAY_TEXT = "More than one ended on the same day: which one do you mean? Ask again with its name and start date."
MOST_FOUND = 50   # enough for the 10th back in four departments, and for the earlier ones with a review
# Words that say what a campaign is or where it falls in time, not which one: no campaign's name has them, so requiring
# them would find nothing. The rewrite is told to leave them out of the words; this holds if it doesn't
_GENERIC = {"a", "an", "the", "our", "my", "we", "campaign", "campaigns", "livestream", "livestreams", "stream",
            "streams", "event", "events", "one", "ones", "show", "broadcast", "last", "latest", "recent", "most",
            "previous", "prior", "before", "that"}

_FINISHED = """
    SELECT campaign_id, campaign_name, department, start_date, end_date
    FROM campaign_summary
    WHERE status = 'finished' AND campaign_name ILIKE ALL (%(patterns)s) {department_filter}
    ORDER BY end_date DESC, start_date DESC, campaign_id
    LIMIT %(limit)s
"""
_IN_DEPARTMENT = "AND department = %(department)s"
_NAMED_DEPARTMENT = "AND department ILIKE %(named)s"
_HAS_REVIEW = "SELECT campaign_id FROM campaigns WHERE campaign_id = ANY(%s) AND has_review_doc"


@dataclass(frozen=True)
class Campaign:
    campaign_id: int
    name: str
    department: str
    start_date: date
    end_date: date

    @property
    def described(self) -> str:
        """How it goes into a question: names repeat every year, so the dates tell them apart."""
        return f"{self.name} (the campaign that ran from {self.start_date.isoformat()} to {self.end_date.isoformat()})"


@dataclass(frozen=True)
class Found:
    campaign: Campaign | None             # the one the question means; None: asked back, or none found
    candidates: tuple[Campaign, ...]      # to ask back with; empty unless several fit
    earlier: tuple[Campaign, ...]         # the ones before it, same words and department, latest first


def patterns(words: str | None) -> list[str]:
    """The ILIKE patterns a name must all match; ['%'] for any campaign."""
    kept = [w for w in re.findall(r"[a-z0-9]+", (words or "").lower()) if w not in _GENERIC]
    return [f"%{w}%" for w in kept] or ["%"]


def chosen(found: list[Campaign], reference: CampaignReference, every_department: bool) -> Found:
    """The rules above, on what the search found, latest end date first."""
    position = reference.position - 1
    if every_department and not reference.department:
        by_department: dict[str, list[Campaign]] = {}
        for campaign in found:
            by_department.setdefault(campaign.department, []).append(campaign)
        if len(by_department) > 1:
            at_position = [c[position] for c in by_department.values() if len(c) > position]
            if len(at_position) > 1:
                return Found(None, tuple(sorted(at_position, key=lambda c: c.end_date, reverse=True)), ())
            found = next((c for c in by_department.values() if len(c) > position), [])
    if position >= len(found):
        return Found(None, (), ())
    campaign = found[position]
    same_day = tuple(c for c in found if c.end_date == campaign.end_date)
    if len(same_day) > 1:
        return Found(None, same_day, ())
    return Found(campaign, (), tuple(found[position + 1:]))


class CampaignFinder:
    def __init__(self, pool: Any, reader_pool: Any):
        self._pool = pool                 # as ops: has_review_doc of campaigns already found
        self._reader_pool = reader_pool   # as sql_reader: the search itself

    async def find(self, reference: CampaignReference, *, role: str, department: str | None,
                   budget: TimeBudget) -> Found:
        asker = visible_department(role, department)   # fails closed, as every path does
        filters = ([_IN_DEPARTMENT] if asker else []) + ([_NAMED_DEPARTMENT] if reference.department else [])
        params = {"patterns": patterns(reference.words), "limit": MOST_FOUND, "department": asker,
                  "named": f"%{reference.department}%"}
        remaining_ms = int(budget.remaining() * 1000)
        if remaining_ms <= 0:
            raise TimeoutError("the time budget ran out before the campaign could be found")
        async with self._reader_pool.connection(timeout=budget.remaining()) as conn:
            result = await run_select(conn, _FINISHED.format(department_filter=" ".join(filters)), params=params,
                                      department=asker, every_department=asker is None, max_rows=MOST_FOUND,
                                      timeout_ms=min(STATEMENT_TIMEOUT_MS, remaining_ms))
        found = [Campaign(int(row[0]), row[1], row[2], date.fromisoformat(row[3]), date.fromisoformat(row[4]))
                 for row in result.rows]
        return chosen(found, reference, every_department=asker is None)

    async def with_review(self, campaigns: tuple[Campaign, ...], budget: TimeBudget) -> set[int]:
        """The ids, among campaigns the search found, that have a post-campaign review."""
        if not campaigns:
            return set()
        async with self._pool.connection(timeout=max(budget.remaining(), 0)) as conn:
            cursor = await conn.execute(_HAS_REVIEW, ([c.campaign_id for c in campaigns],))
            return {row[0] for row in await cursor.fetchall()}
