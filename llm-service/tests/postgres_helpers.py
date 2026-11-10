"""Throwaway databases for tests that need real SQL: every migration applied, dropped at the end, so development
data is never touched."""
import re
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import psycopg
from psycopg.sql import SQL, Identifier, Literal

from app import db

REPO_ROOT = Path(__file__).resolve().parents[2]


def _migration_version(path: Path) -> tuple[int, ...]:
    return tuple(int(part) for part in re.split(r"[._]", path.name[1:].split("__", 1)[0]))


MIGRATIONS = sorted((REPO_ROOT / "db" / "migration").glob("V*__*.sql"), key=_migration_version)


def postgres_available() -> bool:
    try:
        with db.connect() as conn:
            conn.execute("SELECT 1")
        return True
    except psycopg.OperationalError:
        return False


@contextmanager
def throwaway_database() -> Iterator[str]:
    name = f"oka_test_{uuid.uuid4().hex[:8]}"
    with db.connect() as admin:
        admin.execute(f'CREATE DATABASE "{name}"')
    try:
        with db.connect(name) as conn:
            for path in MIGRATIONS:
                conn.execute(path.read_text(encoding="utf-8"))
        yield name
    finally:
        with db.connect() as admin:
            admin.execute(f'DROP DATABASE "{name}" WITH (FORCE)')


@contextmanager
def login_in(group: str) -> Iterator[tuple[str, str]]:
    """A login of its own in a group role of migration V11 (sql_reader or sql_views), dropped at the end, so tests never
    use or change sql_reader's own password. Roles belong to the cluster, hence the unique name; the group exists once
    a throwaway database has had its migrations. A member gets the group's privileges, not its settings: run_select
    sets the read-only transaction and the time limit itself."""
    name, password = f"oka_test_{group}_{uuid.uuid4().hex[:8]}", uuid.uuid4().hex
    with db.connect() as admin:
        admin.execute(SQL("CREATE ROLE {} LOGIN PASSWORD {} IN ROLE {}").format(Identifier(name), Literal(password),
                                                                               Identifier(group)))
    try:
        yield name, password
    finally:
        with db.connect() as admin:
            admin.execute(SQL("DROP ROLE {}").format(Identifier(name)))


def connect_as(dbname: str, login: tuple[str, str]) -> psycopg.Connection:
    user, password = login
    return psycopg.connect(host=db.settings.postgres_host, port=db.settings.postgres_port, dbname=dbname, user=user,
                           password=password, autocommit=True, connect_timeout=db.CONNECT_TIMEOUT_SECONDS)


def seed_two_departments(conn: psycopg.Connection) -> None:
    """A few campaigns in two departments for the Text-to-SQL tests: a finished one with days, products and customers,
    a running one without an actual cost, and one in the other department."""
    conn.execute("INSERT INTO departments (name) VALUES ('Beauty'), ('Fresh Grocery')")
    conn.execute("INSERT INTO hosts (name, department_id, category) VALUES ('Tommy Walter', 1, 'Beauty'), "
                 "('Ana Ruiz', 2, 'Fresh Grocery')")
    conn.execute("""INSERT INTO campaigns (name, department_id, host_id, start_date, end_date, status,
                                           planned_operating_cost, actual_operating_cost) VALUES
                    ('Beauty Summer Sale', 1, 1, '2026-06-12', '2026-07-21', 'finished', 90.00, 100.00),
                    ('Beauty Fan Day', 1, 1, '2026-10-10', '2026-10-30', 'running', 80.00, NULL),
                    ('Grocery Harvest Sale', 2, 2, '2026-09-01', '2026-09-20', 'finished', 50.00, 60.00)""")
    conn.execute("""INSERT INTO campaign_metrics (campaign_id, date, gmv, conversion_rate) VALUES
                    (1, '2026-06-12', 1000.50, 0.1000), (1, '2026-06-13', 2000.00, 0.2000),
                    (2, '2026-10-10', 300.00, 0.0500)""")
    conn.execute("""INSERT INTO campaign_products (campaign_id, product_name, units_sold, profit) VALUES
                    (1, 'Serum', 10, 30.00), (1, 'Lipstick', 20, 40.00), (1, 'Sheet Mask', 30, 50.00),
                    (3, 'Fresh Cherries', 5, 15.00)""")
    conn.execute("""INSERT INTO campaign_customer_behavior (campaign_id, customer_ref, region, ip_address)
                    VALUES (1, 'c-1', 'Midwest', '10.0.0.1'), (3, 'c-2', 'Northeast', '10.0.0.2')""")
