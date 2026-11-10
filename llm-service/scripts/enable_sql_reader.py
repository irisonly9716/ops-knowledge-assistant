"""Lets sql_reader log in with the password in .env (SQL_READER_PASSWORD). Generated SQL runs as this role; migration
V11 creates it without a login, so no password is in a migration.

    cd llm-service
    uv run python -m scripts.enable_sql_reader

Run it once after the migrations, and again after changing the password. The password is hashed here, as psql's
\\password does, and only the hash is sent: the password itself never reaches the server's log or statistics.
"""
import psycopg
from psycopg.sql import SQL, Identifier, Literal

from app import db
from app.config import settings


def enable(conn: psycopg.Connection, role: str, password: str) -> None:
    """ALTER ROLE takes no bound parameter, so the hash is composed into it with sql.Literal, which quotes it the way
    Postgres reads it."""
    hashed = conn.pgconn.encrypt_password(password.encode(), role.encode(), b"scram-sha-256").decode()
    conn.execute(SQL("ALTER ROLE {} LOGIN PASSWORD {}").format(Identifier(role), Literal(hashed)))


def main() -> None:
    if not settings.sql_reader_password:
        raise SystemExit("SQL_READER_PASSWORD is not set: add it to .env at the repository root first")
    with db.connect() as conn:
        if conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (db.SQL_READER,)).fetchone() is None:
            raise SystemExit(f"{db.SQL_READER} doesn't exist yet: run the migrations first "
                             "(docker compose run --rm flyway migrate)")
        enable(conn, db.SQL_READER, settings.sql_reader_password)
    # Logging in proves the password, and shows the role's own limits
    with psycopg.connect(host=settings.postgres_host, port=settings.postgres_port, dbname=settings.postgres_db,
                         user=db.SQL_READER, password=settings.sql_reader_password,
                         connect_timeout=db.CONNECT_TIMEOUT_SECONDS) as reader:
        read_only = reader.execute("SHOW default_transaction_read_only").fetchone()[0]
        timeout = reader.execute("SHOW statement_timeout").fetchone()[0]
    print(f"{db.SQL_READER} can log in (read-only by default: {read_only}, statement timeout: {timeout})")


if __name__ == "__main__":
    main()
