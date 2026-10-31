"""Postgres connections for llm-service (psycopg 3)."""
import psycopg

from app.config import settings


def connect(dbname: str | None = None) -> psycopg.Connection:
    """A new connection in autocommit mode: every statement commits on its own unless it runs inside
    an explicit `with conn.transaction():` block. Transaction boundaries are then visible in the code
    instead of being implied by "whenever commit() happens to be called"."""
    return psycopg.connect(
        host=settings.postgres_host,
        port=settings.postgres_port,
        dbname=dbname or settings.postgres_db,
        user=settings.postgres_user,
        password=settings.postgres_password,
        autocommit=True,
    )
