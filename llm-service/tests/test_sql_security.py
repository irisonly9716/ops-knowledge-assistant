"""The database layer under generated SQL (migration V11), against a throwaway database: the role queries run as reads
the metric views and nothing else, row-level security keeps the asker's department whatever the query says, and
set_config can't change it halfway. As logins of their own in the roles of V11, never sql_reader itself."""
import uuid

import psycopg
import pytest
from postgres_helpers import connect_as, login_in, postgres_available, seed_two_departments, throwaway_database
from psycopg.sql import SQL, Identifier

from app import db
from app.config import settings
from app.sql_agent.execute import run_select
from scripts import enable_sql_reader

needs_postgres = pytest.mark.skipif(not postgres_available(), reason="needs the docker compose Postgres")

# Rows of (Beauty, Fresh Grocery) in seed_two_departments
VIEW_ROWS = {"campaign_summary": (2, 1), "campaign_daily_metrics": (3, 0), "campaign_product_sales": (3, 1),
             "campaign_customers": (1, 1), "host_profiles": (1, 1)}
TABLE_ROWS = {"departments": (1, 1), "hosts": (1, 1), "campaigns": (2, 1), "campaign_metrics": (3, 0),
              "campaign_products": (3, 1), "campaign_customer_behavior": (1, 1)}
BEAUTY, GROCERY, EVERY = "SET LOCAL app.department = 'Beauty'", "SET LOCAL app.department = 'Fresh Grocery'", \
    "SET LOCAL app.all_departments = 'on'"


def none_of(rows: dict) -> dict:
    return dict.fromkeys(rows, 0)


def column(rows: dict, i: int) -> dict:
    return {name: counts[i] for name, counts in rows.items()}


def every(rows: dict) -> dict:
    return {name: sum(counts) for name, counts in rows.items()}


@pytest.fixture(scope="module")
def database():
    if not postgres_available():
        pytest.skip("needs the docker compose Postgres")
    with throwaway_database() as name:
        with db.connect(name) as conn:
            seed_two_departments(conn)
        yield name


@pytest.fixture(scope="module")
def reader(database):
    with login_in("sql_reader") as login:
        yield login


def counts(conn: psycopg.Connection, relations, *setup: str) -> dict[str, int]:
    """Rows of each relation, in one transaction that runs the setup first. Names from the constants above."""
    with conn.transaction():
        for statement in setup:
            conn.execute(statement)
        return {name: conn.execute(f"SELECT count(*) FROM {name}").fetchone()[0] for name in relations}


# ---- what the reader sees


@needs_postgres
def test_with_nothing_set_every_view_is_empty(database, reader):
    with connect_as(database, reader) as conn:   # a connection that never had a department
        assert counts(conn, VIEW_ROWS) == none_of(VIEW_ROWS)


@needs_postgres
def test_each_view_holds_only_the_askers_department_with_no_filter_in_the_query(database, reader):
    departments = " UNION ".join(f"SELECT department FROM {name}" for name in VIEW_ROWS)
    with connect_as(database, reader) as conn:
        assert counts(conn, VIEW_ROWS, BEAUTY) == column(VIEW_ROWS, 0)
        assert counts(conn, VIEW_ROWS, GROCERY) == column(VIEW_ROWS, 1)
        with conn.transaction():
            conn.execute(BEAUTY)
            assert conn.execute(departments).fetchall() == [("Beauty",)]


@needs_postgres
def test_every_department_only_when_asked_for_in_so_many_words(database, reader):
    with connect_as(database, reader) as conn:
        assert counts(conn, VIEW_ROWS, EVERY) == every(VIEW_ROWS)
        for value in ("off", "yes"):
            assert counts(conn, VIEW_ROWS, f"SET LOCAL app.all_departments = '{value}'") == none_of(VIEW_ROWS)


@needs_postgres
def test_a_setting_left_empty_by_an_earlier_transaction_gives_no_rows(database, reader):
    with connect_as(database, reader) as conn:
        counts(conn, VIEW_ROWS, BEAUTY, EVERY)
        assert conn.execute("SELECT current_setting('app.department'), current_setting('app.all_departments')"
                            ).fetchone() == ("", "")
        assert counts(conn, VIEW_ROWS) == none_of(VIEW_ROWS)


@needs_postgres
def test_the_reader_can_read_no_table(database, reader):
    with db.connect(database) as owner:
        tables = [name for (name,) in owner.execute("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")]
    assert set(TABLE_ROWS) | {"users", "documents", "doc_chunks", "sql_examples"} <= set(tables)
    with connect_as(database, reader) as conn:
        for table in tables:
            # Refused for this table itself: a table granted by mistake would still fail, on the departments its policy
            # reads, and that would look the same without the name
            with pytest.raises(psycopg.errors.InsufficientPrivilege, match=rf"permission denied for table {table}$"):
                conn.execute(f"SELECT 1 FROM {table} LIMIT 1")
        with pytest.raises(psycopg.errors.UndefinedColumn):
            conn.execute("SELECT ip_address FROM campaign_customers")


@needs_postgres
def test_set_config_is_refused_so_a_query_cant_change_its_own_department(database, reader):
    with connect_as(database, reader) as conn:
        for sql in ("SELECT set_config('app.department', 'Fresh Grocery', true), "
                    "(SELECT count(*) FROM campaign_summary)",
                    "SELECT pg_catalog.set_config('app.all_departments', 'on', false)",
                    "SELECT set_config('role', 'ops', true)"):
            with pytest.raises(psycopg.errors.InsufficientPrivilege), conn.transaction():
                conn.execute(BEAUTY)
                conn.execute(sql)


@needs_postgres
def test_the_reader_may_write_nothing_whatever_its_transaction(database, reader):
    """A login in the group doesn't get the role's read-only default: these are its privileges alone."""
    with connect_as(database, reader) as conn:
        for view in VIEW_ROWS:
            assert conn.execute("SELECT has_table_privilege(%s::text, 'SELECT'), has_table_privilege(%s::text, "
                                "'INSERT, UPDATE, DELETE, TRUNCATE')", (view, view)).fetchone() == (True, False)
        assert conn.execute("SELECT has_schema_privilege('public', 'CREATE')").fetchone() == (False,)


# ---- the roles and the tables behind the views


@needs_postgres
def test_the_roles_cant_bypass_row_level_security_and_the_views_belong_to_sql_views(database):
    with db.connect(database) as conn:
        roles = {name: rest for name, *rest in conn.execute(
            "SELECT rolname, rolcanlogin, rolsuper, rolbypassrls, rolcreaterole, rolconfig FROM pg_roles "
            "WHERE rolname IN ('sql_reader', 'sql_views')")}
        assert roles["sql_views"] == [False, False, False, False, None]
        assert roles["sql_reader"][1:4] == [False, False, False]   # its login is the script's
        assert {"default_transaction_read_only=on", "statement_timeout=5s"} <= set(roles["sql_reader"][4])
        views = conn.execute("SELECT relname, pg_get_userbyid(relowner), reloptions FROM pg_class "
                             "WHERE relname = ANY(%s) ORDER BY relname", (list(VIEW_ROWS),)).fetchall()
        assert views == [(name, "sql_views", None) for name in sorted(VIEW_ROWS)]   # no security_invoker
        assert conn.execute("SELECT count(*) FROM pg_class WHERE relowner = 'sql_views'::regrole AND relkind = 'r'"
                            ).fetchone() == (0,)
        assert conn.execute(
            "SELECT has_column_privilege('sql_views', 'campaign_customer_behavior', 'ip_address', 'SELECT'), "
            "has_column_privilege('sql_views', 'campaign_customer_behavior', 'region', 'SELECT')").fetchone() == (
            False, True)


@needs_postgres
def test_every_table_behind_the_views_keeps_only_the_askers_rows(database):
    """As the views read them, through a login in sql_views: each table's own policy, so a view added later over any
    one of them keeps to the department too."""
    with login_in("sql_views") as login, connect_as(database, login) as conn:
        assert counts(conn, TABLE_ROWS) == none_of(TABLE_ROWS)
        assert counts(conn, TABLE_ROWS, BEAUTY) == column(TABLE_ROWS, 0)
        assert counts(conn, TABLE_ROWS, GROCERY) == column(TABLE_ROWS, 1)
        assert counts(conn, TABLE_ROWS, EVERY) == every(TABLE_ROWS)


@needs_postgres
def test_the_owner_still_sees_every_row_with_nothing_set(database):
    """ops: the backend, the consumers and the fixed reads of the query path."""
    with db.connect(database) as conn:
        assert counts(conn, TABLE_ROWS) == every(TABLE_ROWS)


# ---- run_select, the pool and the script


@needs_postgres
@pytest.mark.anyio
async def test_run_select_quotes_the_department_and_reads_one_or_every_department(database, reader):
    user, password = reader
    by_department = "SELECT department, count(*) FROM campaign_summary GROUP BY 1 ORDER BY 1"
    async with db.sql_pool(database, user=user, password=password) as pool:
        async with pool.connection() as conn:
            assert (await run_select(conn, by_department, department="Beauty")).rows == (("Beauty", "2"),)
            assert (await run_select(conn, by_department, every_department=True)).rows == (
                ("Beauty", "2"), ("Fresh Grocery", "1"))
            assert (await run_select(conn, by_department)).rows == ()
            hostile = "Beauty' OR 'a' = 'a"
            result = await run_select(conn, "SELECT current_setting('app.department'), count(*) "
                                            "FROM campaign_summary", department=hostile)
            assert result.rows == ((hostile, "0"),)
            with pytest.raises(ValueError):
                await run_select(conn, "SELECT 1", department="Beauty", every_department=True)


def test_the_reader_pool_needs_its_password(monkeypatch):
    monkeypatch.setattr(settings, "sql_reader_password", "")
    with pytest.raises(RuntimeError, match="SQL_READER_PASSWORD"):
        db.sql_pool()


@needs_postgres
def test_enabling_the_reader_sends_only_a_hash_of_its_password(database, monkeypatch):
    name, password = f"oka_test_enable_{uuid.uuid4().hex[:8]}", uuid.uuid4().hex
    with db.connect() as admin:
        admin.execute(SQL("CREATE ROLE {} NOLOGIN").format(Identifier(name)))
    try:
        with db.connect() as admin:
            sent, execute = [], admin.execute

            def recorded(query, *args, **kwargs):
                sent.append(query if isinstance(query, str) else query.as_string(admin))
                return execute(query, *args, **kwargs)

            monkeypatch.setattr(admin, "execute", recorded)
            enable_sql_reader.enable(admin, name, password)
            monkeypatch.undo()
            stored = admin.execute("SELECT rolpassword FROM pg_authid WHERE rolname = %s", (name,)).fetchone()[0]
        assert "SCRAM-SHA-256$" in sent[0] and password not in "".join(sent)
        assert stored.startswith("SCRAM-SHA-256$")
        with connect_as(database, (name, password)) as conn:
            assert conn.execute("SELECT current_user").fetchone() == (name,)
    finally:
        with db.connect() as admin:
            admin.execute(SQL("DROP ROLE {}").format(Identifier(name)))


@needs_postgres
@pytest.mark.skipif(not settings.sql_reader_password, reason="sql_reader has no password here")
def test_migrating_another_database_leaves_the_readers_login_alone(database):
    """database has just had every migration, after sql_reader was enabled: its role statements run on every database,
    and must never turn the login off."""
    with connect_as(settings.postgres_db, (db.SQL_READER, settings.sql_reader_password)) as conn:
        assert conn.execute("SELECT current_user").fetchone() == (db.SQL_READER,)
