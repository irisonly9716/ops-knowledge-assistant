"""Running a generated query: one SELECT, in a read-only transaction, with a time limit and a row limit.

One of the layers generated SQL gets, not enough on its own (AGENTS.md): this one only checks the statement's shape.
The parser checks with their allow-lists (guard.py), the read-only role and row-level security are the others. A query
that tries to write still fails here, because the transaction is read-only.

The transaction also says whose rows the query may read: app.department for staff and managers, read by the department
filter guard.py adds and by the row-level security of migration V11; app.all_departments for executives and admins.
SET LOCAL, so they hold for this transaction only and a pooled connection never carries one asker's department to the
next. Not set_config(..., true), which does the same and takes a bound parameter: it is revoked (V11), because a
function can be called inside a SELECT and change the department halfway through the query, and a statement can't.
SET takes no bound parameter, so the department is composed into it with sql.Literal, which quotes it the way Postgres
reads it: the one value not passed as a parameter (AGENTS.md).
"""
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import psycopg
from psycopg.sql import SQL, Literal

STATEMENT_TIMEOUT_MS = 5000
MAX_ROWS = 200
_SINGLE_SELECT = re.compile(r"(?is)^[\s(]*(select|with)\b")   # a UNION can start with a parenthesis


class SqlRejected(ValueError):
    """The query wasn't run: it isn't something this layer runs."""


@dataclass(frozen=True)
class QueryResult:
    columns: tuple[str, ...]
    rows: tuple[tuple[str | None, ...], ...]   # every value as text, so numbers stay exact
    truncated: bool                            # more rows than max_rows; only the first max_rows are here


def as_text(value) -> str | None:
    return None if value is None else str(value)


async def run_select(conn: psycopg.AsyncConnection, sql: str, *, department: str | None = None,
                     every_department: bool = False, max_rows: int = MAX_ROWS,
                     timeout_ms: int = STATEMENT_TIMEOUT_MS, params: Mapping[str, Any] | None = None) -> QueryResult:
    """Raises SqlRejected for anything but a single SELECT, and psycopg.Error when the database refuses it.
    department: the one department whose rows the query may read; every_department: an asker who may read them all.
    Neither: nothing is set, and as sql_reader every view is empty. params: bound parameters of a query written in code,
    such as the one that finds a campaign (campaigns.py); a model's query has none, as guard.py refuses placeholders."""
    if department is not None and every_department:
        raise ValueError("a query reads one department or every department, not both")
    text = sql.strip().rstrip(";").strip()
    # A semicolon anywhere means a second statement could follow; one inside a string literal is refused too
    if not _SINGLE_SELECT.match(text) or ";" in text:
        raise SqlRejected("only a single SELECT statement can run")
    async with conn.transaction():
        await conn.execute("SET TRANSACTION READ ONLY")
        await conn.execute(f"SET LOCAL statement_timeout = {int(timeout_ms)}")
        if department is not None:
            await conn.execute(SQL("SET LOCAL app.department = {}").format(Literal(department)))
        if every_department:
            await conn.execute("SET LOCAL app.all_departments = 'on'")
        cursor = await conn.execute(text, params)
        columns = tuple(column.name for column in cursor.description or ())
        rows = await cursor.fetchmany(max_rows + 1)
    return QueryResult(columns, tuple(tuple(as_text(v) for v in row) for row in rows[:max_rows]),
                       truncated=len(rows) > max_rows)
