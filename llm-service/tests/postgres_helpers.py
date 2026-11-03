"""Throwaway databases for tests that need real SQL: every migration applied, dropped at the end, so development
data is never touched."""
import re
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import psycopg

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
