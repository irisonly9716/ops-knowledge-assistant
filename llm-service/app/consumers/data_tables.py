"""Structured table uploads: a CSV file -> validated rows for campaign_products / campaign_customer_behavior.

Why these are not chunked or embedded like documents: the questions about them are "how many", "which
product", "what's the average", answered exactly by SQL. Embedding rows of numbers would give approximate
matches on digits. So the rows go straight into their tables, and Text-to-SQL queries them.

Validated cell by cell, so an error says exactly what to fix ("row 3, units_sold: 'abc' is not a whole number").
The csv module for now: at the upload limit pandas is no faster, and only Excel uploads would need it (issue #9).
Amounts are Decimal, not float: the columns are NUMERIC and 0.1 + 0.2 must stay 0.3 on the way in.

A validation failure is a permanent error: the same file fails the same way, however often it is retried.
All problems are reported at once (up to a limit), so the uploader doesn't fix them one upload at a time.
"""
import csv
import io
import ipaddress
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb

from app.rag import store

MAX_REPORTED_PROBLEMS = 10
MAX_SHOWN_CELL_CHARS = 50
MAX_INTEGER = 2_147_483_647          # the INTEGER column type
CAMPAIGN_ID_COLUMN = "campaign_id"   # optional in the file; if present, it must be the upload's campaign


class TableValidationError(Exception):
    """The file can't be loaded as it is. The message says what to fix."""


class TableConflict(Exception):
    """The version this upload was meant to replace is no longer the current one. Retrying gives the same answer."""


def _text(raw: str) -> str:
    return raw


def _optional_text(raw: str) -> str | None:
    return raw or None


def _whole_number(raw: str) -> int:
    if not raw.isascii() or not raw.isdigit():
        raise ValueError("is not a whole number")
    if len(raw) > 10 or int(raw) > MAX_INTEGER:
        raise ValueError(f"is out of range (at most {MAX_INTEGER})")
    return int(raw)


def _number(lowest: str, highest: str) -> Callable[[str], Decimal]:
    """A number in the range of its NUMERIC column. A value the column can't hold is caught here, with a message
    a person can act on; in the database it would be an error that looks temporary and gets retried. highest is
    the largest value the column holds, so Postgres rounding to the column's scale can't overflow: 999.995
    doesn't fit NUMERIC(5, 2) and is refused, 999.99 does."""
    low, high = Decimal(lowest), Decimal(highest)

    def parse(raw: str) -> Decimal:
        try:
            value = Decimal(raw)
        except InvalidOperation:
            raise ValueError("is not a number") from None
        if not value.is_finite():   # Decimal accepts "NaN" and "Infinity"
            raise ValueError("is not a number")
        if not low <= value <= high:
            raise ValueError(f"is out of range ({lowest} to {highest})")
        return value
    return parse


_RATE = _number("0", "1")                                       # a share; NUMERIC(5, 4)
_MARGIN = _number("-9.9999", "9.9999")                          # NUMERIC(5, 4); negative: sold at a loss
_SALES = _number("0", "999999999999.99")                        # NUMERIC(14, 2)
_PROFIT = _number("-999999999999.99", "999999999999.99")        # NUMERIC(14, 2)
_PRICE = _number("0", "99999999.99")                            # NUMERIC(10, 2)
_MINUTES = _number("0", "9999.99")                              # NUMERIC(6, 2)


def _products(raw: str) -> Jsonb:
    """The products a customer bought, separated by semicolons: "Serum;Lipstick". Empty: bought nothing."""
    return Jsonb([name.strip() for name in raw.split(";") if name.strip()])


def _ip_address(raw: str) -> str | None:
    if not raw:
        return None
    try:
        ipaddress.ip_address(raw)
    except ValueError:
        raise ValueError("is not an IP address") from None
    return raw


@dataclass(frozen=True)
class Column:
    name: str
    parse: Callable[[str], Any]   # optional columns also get the empty cell, and decide what it means
    required: bool = True


@dataclass(frozen=True)
class TableSpec:
    columns: tuple[Column, ...]
    unique_column: str | None = None   # must not repeat within a file (the table's UNIQUE constraint)


# Keys are the values of uploads.table_type, and the table names themselves
TABLES = {
    "campaign_products": TableSpec(
        columns=(
            Column("product_name", _text),
            Column("units_sold", _whole_number),
            Column("sell_through_rate", _RATE),
            Column("return_rate", _RATE),
            Column("sales_amount", _SALES),
            Column("click_through_rate", _RATE),
            Column("cost_price", _PRICE),
            Column("profit", _PROFIT),
            Column("profit_margin", _MARGIN),
        ),
        unique_column="product_name",
    ),
    "campaign_customer_behavior": TableSpec(
        columns=(
            Column("customer_ref", _text),
            Column("region", _optional_text, required=False),
            Column("dwell_time_minutes", _MINUTES),
            Column("products_purchased", _products, required=False),
            Column("purchase_amount", _PRICE),
            Column("ip_address", _ip_address, required=False),
        ),
    ),
}


def parse_table(table_type: str, data: bytes, campaign_id: int) -> list[tuple[Any, ...]]:
    """The file's rows as tuples in the order of the table's columns, or TableValidationError saying what is wrong."""
    spec = TABLES[table_type]
    try:
        text = data.decode("utf-8-sig")   # -sig: Excel's "CSV UTF-8" starts with a BOM
    except UnicodeDecodeError:
        raise TableValidationError("the file is not UTF-8; save it as CSV UTF-8 and upload it again") from None
    if "\x00" in text:   # a Postgres text value can't hold it, and a text CSV doesn't have it
        raise TableValidationError("the file contains NUL bytes, so it is not a text CSV; "
                                   "save it as CSV UTF-8 and upload it again")

    reader = csv.reader(io.StringIO(text, newline=""))
    try:
        header = [name.strip() for name in next(reader, [])]
        positions = _check_header(header, spec)
        rows, problems = _read_rows(reader, header, positions, spec, campaign_id)
    except csv.Error as e:
        raise TableValidationError(f"the file is not a valid CSV: {e}") from e

    if problems:
        shown = "; ".join(problems[:MAX_REPORTED_PROBLEMS])
        more = len(problems) - MAX_REPORTED_PROBLEMS
        raise TableValidationError(shown + (f"; and {more} more problem(s)" if more > 0 else ""))
    if not rows:
        raise TableValidationError("the file has a header but no data rows")
    return rows


def _check_header(header: list[str], spec: TableSpec) -> dict[str, int]:
    expected = [column.name for column in spec.columns]
    missing = [name for name in expected if name not in header]
    unknown = [name for name in header if name not in expected and name != CAMPAIGN_ID_COLUMN]
    if missing or unknown:
        details = []
        if missing:
            details.append(f"missing columns: {', '.join(missing)}")
        if unknown:
            details.append(f"unknown columns: {', '.join(unknown)}")
        raise TableValidationError("; ".join(details) + f" (expected: {', '.join(expected)})")
    repeated = sorted({name for name in header if header.count(name) > 1})
    if repeated:
        raise TableValidationError(f"repeated columns: {', '.join(repeated)}")
    return {name: position for position, name in enumerate(header)}


def _read_rows(reader, header: list[str], positions: dict[str, int], spec: TableSpec,
               campaign_id: int) -> tuple[list[tuple[Any, ...]], list[str]]:
    rows: list[tuple[Any, ...]] = []
    problems: list[str] = []
    seen_unique: set[str] = set()
    campaign_position = positions.get(CAMPAIGN_ID_COLUMN)
    for cells in reader:
        if not any(cell.strip() for cell in cells):
            continue   # a blank line, common at the end of exported files
        where = f"row {reader.line_num}"   # the row number a spreadsheet shows: the header is row 1
        if len(cells) != len(header):
            problems.append(f"{where} has {len(cells)} cells, expected {len(header)}")
            continue

        if campaign_position is not None and cells[campaign_position].strip() != str(campaign_id):
            problems.append(f"{where}, {CAMPAIGN_ID_COLUMN}: {_shown(cells[campaign_position])} "
                            f"is not this upload's campaign ({campaign_id})")

        values: list[Any] = []
        for column in spec.columns:
            raw = cells[positions[column.name]].strip()
            if not raw and column.required:
                problems.append(f"{where}, {column.name}: is empty")
                values.append(None)
                continue
            try:
                values.append(column.parse(raw))
            except ValueError as e:
                problems.append(f"{where}, {column.name}: {_shown(raw)} {e}")
                values.append(None)

        if spec.unique_column:
            key = cells[positions[spec.unique_column]].strip()
            if key and key in seen_unique:
                problems.append(f"{where}, {spec.unique_column}: {_shown(key)} appears more than once")
            seen_unique.add(key)
        rows.append(tuple(values))
    return rows, problems


def _shown(cell: str) -> str:
    return repr(cell.strip()[:MAX_SHOWN_CELL_CHARS])


def current_version(conn: psycopg.Connection, table_type: str, campaign_id: int) -> str | None:
    """The upload behind the rows currently in a table for a campaign: the last one that finished. A data table
    has no documents row to point at its current version. None: no upload yet (mock data came without one)."""
    row = conn.execute(
        """SELECT upload_id::text FROM uploads
           WHERE table_type = %s AND campaign_id = %s AND status = 'done'
           ORDER BY updated_at DESC LIMIT 1""",
        (table_type, campaign_id),
    ).fetchone()
    return row[0] if row else None


def save_rows(conn: psycopg.Connection, upload: store.UploadRow, rows: Sequence[Sequence[Any]]) -> int:
    """Replace a campaign's rows in a data table with this upload's rows and mark it done. Returns how many rows
    were there before. Runs in the caller's transaction, so readers see all the old rows or all the new ones.

    The same rule as documents: an upload names the version it replaces (replaces_upload_id; empty when there
    is none). If another upload finished in the meantime, this one is out of date and may not overwrite it.
    The campaign row lock makes two uploads for one campaign take turns, otherwise both could pass the check and
    then both write. NO KEY: the backend's foreign key checks on the campaign don't have to wait for this one.
    """
    conn.execute("SELECT 1 FROM campaigns WHERE campaign_id = %s FOR NO KEY UPDATE", (upload.campaign_id,))
    current = current_version(conn, upload.table_type, upload.campaign_id)
    if current != upload.replaces_upload_id:
        raise TableConflict(f"this upload was based on version {upload.replaces_upload_id}, but the current "
                            f"{upload.table_type} data of campaign {upload.campaign_id} came from {current}")

    # Identifier quotes the name as SQL expects; the table name can't come from anywhere but the TABLES keys
    table = sql.Identifier(upload.table_type)
    columns = ["campaign_id", *(column.name for column in TABLES[upload.table_type].columns)]
    delete = sql.SQL("DELETE FROM {} WHERE campaign_id = %s").format(table)
    copy = sql.SQL("COPY {} ({}) FROM STDIN").format(table, sql.SQL(", ").join(map(sql.Identifier, columns)))

    replaced = conn.execute(delete, (upload.campaign_id,)).rowcount
    # COPY, not executemany: the whole load is one statement, so statement_timeout limits all of it. Measured:
    # executemany ran 4.3 s under a 0.2 s limit, because the limit applies to each INSERT and not to the batch
    with conn.cursor() as cur, cur.copy(copy) as loader:
        for row in rows:
            loader.write_row((upload.campaign_id, *row))
    store.set_status(conn, upload.upload_id, "done")
    return replaced
