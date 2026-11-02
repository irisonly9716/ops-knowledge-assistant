"""Postgres connections for llm-service (psycopg 3)."""
import psycopg

from app.config import settings

CONNECT_TIMEOUT_SECONDS = 10
STATEMENT_TIMEOUT_SECONDS = 30
LOCK_TIMEOUT_SECONDS = 10


def connect(dbname: str | None = None) -> psycopg.Connection:
    """A new connection in autocommit mode: every statement commits on its own unless it runs inside
    an explicit `with conn.transaction():` block. Transaction boundaries are then visible in the code
    instead of being implied by "whenever commit() happens to be called".

    Every wait is bounded, so a stuck database can't keep a consumer past max.poll.interval.ms: connecting,
    a single statement, and waiting for a row lock each give up with an error (which is then retried).
    """
    return psycopg.connect(
        host=settings.postgres_host,
        port=settings.postgres_port,
        dbname=dbname or settings.postgres_db,
        user=settings.postgres_user,
        password=settings.postgres_password,
        autocommit=True,
        connect_timeout=CONNECT_TIMEOUT_SECONDS,
        options=f"-c statement_timeout={STATEMENT_TIMEOUT_SECONDS * 1000} -c lock_timeout={LOCK_TIMEOUT_SECONDS * 1000}",
    )
