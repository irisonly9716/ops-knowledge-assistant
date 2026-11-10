"""The checks a generated query passes before it runs, and the department filter it is given.

One of the layers generated SQL gets (AGENTS.md), and not enough on its own: the read-only transaction, the role and
row-level security are others. This one parses the query with sqlglot and allows only what a question about campaigns
needs: one SELECT, reading only the relations it was offered, calling only functions on an allow-list, casting only to
plain types. Allow-lists, not block-lists: a list of dangerous functions is never complete, and set_config alone could
change the department a query is filtered to.

What runs is the query regenerated from the checked tree, never the model's text, so what was checked and what runs
can't differ (on the development data, 167 queries regenerated this way returned the same rows as their originals).

For staff and managers every relation the query reads is replaced by a subquery holding only their department's rows,
whatever the query says. The department is not in the SQL text: the subquery reads it from the setting app.department,
which the code sets in the same transaction (execute.run_select), and which row-level security reads too (migration
V11). A query can't change that setting itself, because set_config and current_setting aren't on the function
allow-list, and the database refuses set_config to the role queries run as. Without the setting a filtered query reads
no department: on a connection that never had it, current_setting is an error; on one that did, it stays defined
there, empty, and matches no rows.

Every query returns at most ROW_LIMIT rows: the outer query gets a LIMIT, or keeps its own if that is smaller.
"""
from collections.abc import Mapping

import sqlglot
from sqlglot import exp

from app.sql_agent.execute import MAX_ROWS, SqlRejected

# A relation and the subquery that keeps one department's rows of it. Every metric view has the department's name
DEPARTMENT = "current_setting('app.department')"
METRIC_VIEWS = {name: f"SELECT * FROM {name} WHERE department = {DEPARTMENT}"
                for name in ("campaign_summary", "campaign_daily_metrics", "campaign_product_sales",
                             "campaign_customers", "host_profiles")}

# Functions by sqlglot's class for them; Anonymous is any function sqlglot has no class for, allowed by name below.
# What questions about campaigns need: logic, aggregates, window functions, arithmetic, dates, text and the JSON array
# of a customer's products. Not repeat or lpad, which can build a huge string before the time limit stops them
ALLOWED_FUNCTIONS = frozenset("""
    And Or Xor Case If Coalesce Nullif Greatest Least Cast Exists
    Sum Avg Count Min Max Stddev StddevPop StddevSamp Variance VariancePop PercentileCont PercentileDisc Median
    ArrayAgg GroupConcat Corr CountIf LogicalAnd LogicalOr
    RowNumber Rank DenseRank PercentRank CumeDist Ntile Lag Lead FirstValue LastValue NthValue
    Round Abs Ceil Floor Sqrt Pow Ln Log Exp Sign Trunc
    Extract CurrentDate CurrentTimestamp TimestampTrunc DateTrunc TimeToStr StrToDate StrToTime UnixToTime Date
    DateAdd DateSub DateDiff ToChar ExplodingGenerateSeries GenerateSeries
    Concat ConcatWs Length Lower Upper Substring Trim Replace SplitPart StrPosition Left Right Initcap
    JSONExtract JSONExtractScalar JSONBExtract JSONBExtractScalar ArrayContainsAll JSONBContains ArraySize Array
    Unnest Explode
""".split())
ALLOWED_ANONYMOUS = frozenset({"age", "make_date", "date_part", "jsonb_array_elements_text", "jsonb_array_length",
                               "jsonb_agg", "jsonb_build_object", "json_agg", "json_build_object"})
# Casts only to plain types: not regclass or oid, which turn names into the catalog's objects
ALLOWED_TYPES = frozenset({
    exp.DataType.Type.SMALLINT, exp.DataType.Type.INT, exp.DataType.Type.BIGINT, exp.DataType.Type.DECIMAL,
    exp.DataType.Type.FLOAT, exp.DataType.Type.DOUBLE, exp.DataType.Type.TEXT, exp.DataType.Type.VARCHAR,
    exp.DataType.Type.CHAR, exp.DataType.Type.DATE, exp.DataType.Type.TIMESTAMP, exp.DataType.Type.TIMESTAMPTZ,
    exp.DataType.Type.TIME, exp.DataType.Type.INTERVAL, exp.DataType.Type.BOOLEAN, exp.DataType.Type.JSON,
    exp.DataType.Type.JSONB, exp.DataType.Type.ARRAY,
})
# Anywhere in the tree: writing (also inside WITH), SELECT INTO, FOR UPDATE, and parameters nobody would bind
_FORBIDDEN = (exp.DML, exp.DDL, exp.Command, exp.Into, exp.Lock, exp.Placeholder, exp.Parameter)
# One more than run_select keeps, so it can still tell a result was cut off. In the query, so Postgres stops there:
# psycopg receives a whole result before handing over any of it, and a query returning 2 million rows took 2.0 s and
# 156 MB more memory to keep 200 of them, against 0.2 s and none with this limit
ROW_LIMIT = MAX_ROWS + 1


def checked(sql: str, relations: Mapping[str, str], *, one_department: bool) -> str:
    """The query to run, or SqlRejected saying why not, worded for the model that wrote it. relations: the ones the
    query may read, each with the subquery that keeps one department's rows. one_department: replace every relation
    the query reads with that subquery."""
    try:
        statements = [s for s in sqlglot.parse(sql, read="postgres") if s is not None]
    except sqlglot.errors.ParseError as error:
        # Where and what only: the message itself underlines the place with terminal escape codes
        first = error.errors[0] if error.errors else {}
        raise SqlRejected(f"the query doesn't parse: {first.get('description', 'invalid SQL')} at line "
                          f"{first.get('line')}, column {first.get('col')}") from None
    except sqlglot.errors.SqlglotError as error:
        raise SqlRejected(f"the query doesn't parse: {str(error)[:300]}") from None
    if len(statements) != 1:
        raise SqlRejected("write exactly one statement")
    tree = statements[0]
    if not isinstance(tree, exp.Query):
        raise SqlRejected("only a SELECT query can run")

    for node in tree.walk():
        if isinstance(node, _FORBIDDEN):
            raise SqlRejected(f"not allowed in a query: {node.key.upper()}")
        if isinstance(node, exp.With) and node.args.get("recursive"):
            raise SqlRejected("WITH RECURSIVE is not allowed")
        if isinstance(node, exp.CTE) and _name(node.args["alias"].this) in relations:
            raise SqlRejected(f"a WITH query can't have the name of a view: {node.alias}")
        if isinstance(node, exp.Anonymous):
            if node.name.lower() not in ALLOWED_ANONYMOUS:
                raise SqlRejected(f"function not allowed: {node.name}")
        elif isinstance(node, exp.Func) and type(node).__name__ not in ALLOWED_FUNCTIONS:
            raise SqlRejected(f"function not allowed: {node.sql(dialect='postgres')[:60]}")
        if isinstance(node, exp.DataType) and node.this not in ALLOWED_TYPES:
            raise SqlRejected(f"type not allowed: {node.sql(dialect='postgres')}")
        if isinstance(node, exp.Table) and isinstance(node.this, exp.Identifier):
            _check_relation(node, relations)

    if one_department:
        tree = tree.transform(lambda node: _one_department(node, relations))
    _limit_rows(tree, ROW_LIMIT)
    return tree.sql(dialect="postgres", comments=False)


def _name(identifier: exp.Identifier) -> str:
    """As Postgres reads it: unquoted names are folded to lower case."""
    return identifier.this if identifier.quoted else identifier.this.lower()


def _check_relation(table: exp.Table, relations: Mapping[str, str]) -> None:
    name = _name(table.this)
    if table.args.get("db") or table.args.get("catalog"):
        raise SqlRejected(f"use the view's name alone, without a schema: {table.sql(dialect='postgres')}")
    if name not in relations and name not in _visible_ctes(table):
        raise SqlRejected(f"no such view: {name}. The views are: {', '.join(relations)}")


def _visible_ctes(table: exp.Table) -> set[str]:
    """The WITH queries a name in the FROM clause can refer to where it stands. Inside a WITH query only the ones
    before it are visible: anything else would name a real table, so a WITH query of the same name further on must not
    make it look allowed."""
    names: set[str] = set()
    child, node = table, table.parent
    while node is not None:
        if isinstance(node, exp.With):
            ctes = node.expressions
            position = next((i for i, cte in enumerate(ctes) if cte is child), len(ctes))
            names |= {_name(cte.args["alias"].this) for cte in ctes[:position]}
        elif isinstance(node, exp.Query) and node.args.get("with_") is not None and node.args["with_"] is not child:
            names |= {_name(cte.args["alias"].this) for cte in node.args["with_"].expressions}
        child, node = node, node.parent
    return names


def _limit_rows(tree: exp.Query, most: int) -> None:
    """The outer query returns at most `most` rows. A limit the query has keeps its meaning: a smaller number stays, one
    that isn't a number is capped with LEAST. Limits inside it are left alone: only the outer one decides how many rows
    come back."""
    limit = tree.args.get("limit")
    if isinstance(limit, exp.Fetch):
        options = limit.args.get("limit_options")
        if options is not None and options.args.get("with_ties"):
            # Ties can return any number of rows past the count, and LIMIT has no such form
            raise SqlRejected("FETCH ... WITH TIES is not allowed: use LIMIT")
        if limit.args.get("count") is not None:
            limit.set("count", _at_most(limit.args["count"], most))
    elif limit is None or (isinstance(limit.expression, exp.Var) and limit.expression.name.upper() == "ALL"):
        tree.set("limit", exp.Limit(expression=exp.Literal.number(most)))
    else:
        limit.set("expression", _at_most(limit.expression, most))


def _at_most(count: exp.Expression, most: int) -> exp.Expression:
    if isinstance(count, exp.Literal) and count.is_int:
        return exp.Literal.number(min(int(count.name), most))
    return exp.Least(this=count.copy(), expressions=[exp.Literal.number(most)])


def _one_department(node: exp.Expression, relations: Mapping[str, str]) -> exp.Expression:
    """A relation the query reads becomes its one-department subquery, under the name the query uses for it. A WITH
    query can't have a relation's name (checked above), so every such name here is the relation itself."""
    if not (isinstance(node, exp.Table) and isinstance(node.this, exp.Identifier)):
        return node
    name = _name(node.this)
    if name not in relations:
        return node
    alias = node.args.get("alias")
    alias = alias.copy() if alias is not None else exp.TableAlias(this=exp.to_identifier(name))
    return exp.Subquery(this=sqlglot.parse_one(relations[name], read="postgres"), alias=alias)
