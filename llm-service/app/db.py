"""Postgres connections for llm-service (psycopg 3)."""
import asyncio
import sys

import psycopg
from psycopg_pool import AsyncConnectionPool

from app.config import settings

CONNECT_TIMEOUT_SECONDS = 10
STATEMENT_TIMEOUT_SECONDS = 30
LOCK_TIMEOUT_SECONDS = 10
QUERY_STATEMENT_TIMEOUT_SECONDS = 5
QUERY_POOL_MIN_SIZE = 4


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


def query_pool(dbname: str | None = None) -> AsyncConnectionPool:
    """The connections of the query path, unopened. A pool because a new connection took 10.7 ms and a checkout
    0.4 ms, and because 300 queries at once without one failed: Postgres allows 100 connections.

    Read-only by default, so a bug here can't write, and with a shorter statement limit than ingestion: a query has
    a user waiting. Each checkout tests the connection, about 0.4 ms. Measured after Postgres killed every
    connection: without the test, as many queries fail as there were connections; with it none do. They wait for a
    new connection instead, 0.07 s under concurrent load and 6.8 s for a lone query, which is inside its budget.
    """
    conninfo = psycopg.conninfo.make_conninfo(
        host=settings.postgres_host,
        port=settings.postgres_port,
        dbname=dbname or settings.postgres_db,
        user=settings.postgres_user,
        password=settings.postgres_password,
        connect_timeout=CONNECT_TIMEOUT_SECONDS,
        options=f"-c statement_timeout={QUERY_STATEMENT_TIMEOUT_SECONDS * 1000} -c default_transaction_read_only=on",
    )
    return AsyncConnectionPool(conninfo, min_size=QUERY_POOL_MIN_SIZE, max_size=settings.query_pool_size, open=False,
                               kwargs={"autocommit": True}, check=AsyncConnectionPool.check_connection)


def require_compatible_event_loop() -> None:
    """Async psycopg can't run on Windows' default event loop. Better a clear message at startup than the same
    error on every query (see app/server.py)."""
    if sys.platform == "win32" and isinstance(asyncio.get_running_loop(), asyncio.ProactorEventLoop):
        raise RuntimeError("async psycopg can't run on ProactorEventLoop: start the API with `python -m app.server`")
