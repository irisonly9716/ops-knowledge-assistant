"""A time named relative to today, as dates: worked out in code, never by a model.

The wording put back into the question names the dates themselves, so the SQL writer filters on them as given and the
same question asked next month reads differently, which the cache's key will need.
"""
import calendar
from dataclasses import dataclass
from datetime import date

from app.router.rewriting import PeriodReference


@dataclass(frozen=True)
class Period:
    start: date
    end: date      # the last day, included
    text: str      # what goes into the question in place of {period}


def _month(year: int, month: int) -> tuple[date, date]:
    return date(year, month, 1), date(year, month, calendar.monthrange(year, month)[1])


def _months_back(day: date, months: int) -> tuple[int, int]:
    """The year and month `months` before day's month."""
    index = day.year * 12 + day.month - 1 - months
    return index // 12, index % 12 + 1


def resolve(reference: PeriodReference, today: date) -> Period:
    kind = reference.kind
    if kind in ("this_month", "last_month"):
        start, end = _month(*_months_back(today, 0 if kind == "this_month" else 1))
        return Period(start, end, f"{start:%B %Y} ({start.isoformat()} to {end.isoformat()})")
    if kind in ("this_year", "last_year"):
        year = today.year - 1 if kind == "last_year" else today.year
        start, end = date(year, 1, 1), date(year, 12, 31)
        return Period(start, end, f"{year} ({start.isoformat()} to {end.isoformat()})")
    if kind == "last_full_months":
        months = reference.months or 0
        if months < 1:
            raise ValueError("last_full_months needs a number of months")
        start = _month(*_months_back(today, months))[0]
        end = _month(*_months_back(today, 1))[1]
        return Period(start, end, f"the {months} full months from {start.isoformat()} to {end.isoformat()}")
    raise ValueError(f"no such period: {kind}")
