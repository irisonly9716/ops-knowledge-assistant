"""What a model writing SQL is told about the database: the tables or views it may query and their columns, and,
when asked for, the data dictionary (the comments of migration V9) and the values found in the columns questions
name things by.

Read from the catalog at run time, so a model sees the definitions in the migrations, never a copy that drifts from
them. ip_address is never shown: it is personal data, and a column a model is never told about doesn't end up in
its queries.
"""
from collections.abc import Sequence
from dataclasses import dataclass

import psycopg

from app.rag.retrieval import ROLES_LIMITED_TO_A_DEPARTMENT

BASE_TABLES = ("departments", "hosts", "campaigns", "campaign_metrics", "campaign_products",
               "campaign_customer_behavior")
METRIC_VIEWS = ("campaign_summary", "campaign_daily_metrics", "campaign_product_sales", "campaign_customers",
                "host_profiles")
HIDDEN_COLUMNS = frozenset({("campaign_customer_behavior", "ip_address")})
# Per kind of value. 4 departments hold 50 campaigns today; 8 departments over several years would make the lists
# long, and the prompt pays for every name in them
MAX_VALUES = 100

_COLUMNS = """
    SELECT c.relname, c.relkind, obj_description(c.oid, 'pg_class'), a.attname,
           format_type(a.atttypid, a.atttypmod), col_description(c.oid, a.attnum)
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped
    WHERE n.nspname = 'public' AND c.relkind IN ('r', 'v') AND c.relname = ANY(%(names)s)
    ORDER BY c.relname, a.attnum
"""

# Each list keeps to what the asker may see: a staff member is shown the names of their own department's campaigns
# and products, never another department's. NULL department: every department
_VALUES = {
    "departments": """
        SELECT name FROM departments
        WHERE %(department)s::text IS NULL OR name = %(department)s
        ORDER BY name LIMIT %(limit)s""",
    "campaign names": """
        SELECT c.name FROM campaigns c JOIN departments d ON d.department_id = c.department_id
        WHERE %(department)s::text IS NULL OR d.name = %(department)s
        GROUP BY c.name ORDER BY max(c.start_date) DESC, c.name LIMIT %(limit)s""",
    "product names": """
        SELECT p.product_name FROM campaign_products p
        JOIN campaigns c ON c.campaign_id = p.campaign_id JOIN departments d ON d.department_id = c.department_id
        WHERE %(department)s::text IS NULL OR d.name = %(department)s
        GROUP BY p.product_name ORDER BY p.product_name LIMIT %(limit)s""",
    "regions": """
        SELECT b.region FROM campaign_customer_behavior b
        JOIN campaigns c ON c.campaign_id = b.campaign_id JOIN departments d ON d.department_id = c.department_id
        WHERE b.region IS NOT NULL AND (%(department)s::text IS NULL OR d.name = %(department)s)
        GROUP BY b.region ORDER BY b.region LIMIT %(limit)s""",
}


@dataclass(frozen=True)
class Column:
    name: str
    type: str
    comment: str | None


@dataclass(frozen=True)
class Relation:
    name: str
    kind: str              # table | view
    comment: str | None
    columns: tuple[Column, ...]


async def relations(conn: psycopg.AsyncConnection, names: Sequence[str]) -> list[Relation]:
    """The tables or views, in the order asked for. An unknown name is an error, not a smaller schema."""
    cursor = await conn.execute(_COLUMNS, {"names": list(names)})
    found: dict[str, Relation] = {}
    for relname, relkind, comment, column, column_type, column_comment in await cursor.fetchall():
        if (relname, column) in HIDDEN_COLUMNS:
            continue
        relation = found.get(relname) or Relation(relname, "view" if relkind == "v" else "table", comment, ())
        found[relname] = Relation(relation.name, relation.kind, relation.comment,
                                  (*relation.columns, Column(column, column_type, column_comment)))
    missing = [name for name in names if name not in found]
    if missing:
        raise ValueError(f"not in the database: {', '.join(missing)}")
    return [found[name] for name in names]


def describe(relations: Sequence[Relation], *, comments: bool) -> str:
    """One block per table or view: its columns and their types, and with comments each one's meaning."""
    blocks = []
    for relation in relations:
        lines = [f"{relation.kind.upper()} {relation.name}"
                 + (f" -- {relation.comment}" if comments and relation.comment else "")]
        lines += [f"  {c.name} {c.type}" + (f" -- {c.comment}" if comments and c.comment else "")
                  for c in relation.columns]
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def visible_department(role: str, department: str | None) -> str | None:
    """The one department whose values the asker may see, or None for all of them. Fails closed like retrieval."""
    if role in ROLES_LIMITED_TO_A_DEPARTMENT:
        if not department:
            raise ValueError(f"a {role} needs a department")
        return department
    if role in ("executive", "admin"):
        return None
    raise ValueError(f"unknown role: {role}")


async def known_values(conn: psycopg.AsyncConnection, role: str, department: str | None) -> dict[str, list[str]]:
    """The names questions refer to things by, as they are written in the data: a model that knows the campaign is
    called "Electronics Black Friday Live" doesn't have to guess it from "last Black Friday's electronics stream"."""
    params = {"department": visible_department(role, department), "limit": MAX_VALUES}
    values = {}
    for kind, sql in _VALUES.items():
        cursor = await conn.execute(sql, params)
        values[kind] = [row[0] for row in await cursor.fetchall()]
    return values


def describe_values(values: dict[str, list[str]]) -> str:
    lines = ["Values in the data:"]
    for kind, names in values.items():
        more = " (and more)" if len(names) >= MAX_VALUES else ""
        lines.append(f"  {kind}: {', '.join(names) if names else 'none'}{more}")
    return "\n".join(lines)
