"""Running a generated query: one SELECT, in a read-only transaction, with a time limit and a row limit.

One of the layers generated SQL gets, not enough on its own (AGENTS.md): this one only checks the statement's shape.
The parser checks with their allow-lists (guard.py), the read-only role and row-level security are the others. A query
that tries to write still fails here, because the transaction is read-only.

For staff and managers the transaction also sets app.department, the setting read by the department filter guard.py
adds. SET LOCAL can't take a bound parameter, so it is set with set_config(..., true): the same, local to the
transaction, so a pooled connection never carries one asker's department to the next.
"""
import re
from dataclasses import dataclass

import psycopg

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
                     max_rows: int = MAX_ROWS, timeout_ms: int = STATEMENT_TIMEOUT_MS) -> QueryResult:
    """Raises SqlRejected for anything but a single SELECT, and psycopg.Error when the database refuses it.
    department: the value of app.department for this query, or None to leave it unset."""
    text = sql.strip().rstrip(";").strip()
    # A semicolon anywhere means a second statement could follow; one inside a string literal is refused too
    if not _SINGLE_SELECT.match(text) or ";" in text:
        raise SqlRejected("only a single SELECT statement can run")
    async with conn.transaction():
        await conn.execute("SET TRANSACTION READ ONLY")
        await conn.execute(f"SET LOCAL statement_timeout = {int(timeout_ms)}")
        if department is not None:
            await conn.execute("SELECT set_config('app.department', %s, true)", (department,))
        cursor = await conn.execute(text)
        columns = tuple(column.name for column in cursor.description or ())
        rows = await cursor.fetchmany(max_rows + 1)
    return QueryResult(columns, tuple(tuple(as_text(v) for v in row) for row in rows[:max_rows]),
                       truncated=len(rows) > max_rows)
