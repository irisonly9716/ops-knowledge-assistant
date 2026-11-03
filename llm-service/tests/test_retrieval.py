"""Retrieval against a real Postgres: who sees what, order, and that the approximate index doesn't lose documents.

Vectors are built so the expected similarity is known exactly: near(0.9) has cosine similarity 0.9 with QUESTION.
Needs the docker compose Postgres; skipped when it isn't reachable.
"""
import asyncio
import math
import random
import time
import uuid

import psycopg
import pytest
from postgres_helpers import postgres_available, throwaway_database

from app import db
from app.config import settings
from app.rag.embeddings import EMBEDDING_DIMENSIONS
from app.rag.retrieval import search, search_sql, visible_departments
from app.rag.store import vector_literal

pytestmark = pytest.mark.skipif(not postgres_available(), reason="needs the docker compose Postgres")

MODEL = "text-embedding-3-small"
QUESTION = [1.0] + [0.0] * (EMBEDDING_DIMENSIONS - 1)


def near(similarity: float) -> list[float]:
    return [similarity, math.sqrt(1 - similarity ** 2)] + [0.0] * (EMBEDDING_DIMENSIONS - 2)


def add_document(conn, department: str, title: str, vectors: list[list[float]], model: str = MODEL) -> int:
    upload_id = str(uuid.uuid4())
    conn.execute("""INSERT INTO uploads (upload_id, source, uploaded_by, content_kind, doc_type, department, title,
                                         original_filename, storage_key, status)
                    VALUES (%s, 'manual', 1, 'document', 'sop', %s, %s, 'x.md', %s, 'done')""",
                 (upload_id, department, title, upload_id))
    doc_id = conn.execute("""INSERT INTO documents (doc_type, title, department, upload_id)
                             VALUES ('sop', %s, %s, %s) RETURNING doc_id""", (title, department, upload_id)).fetchone()[0]
    with conn.cursor() as cur, cur.copy("""COPY doc_chunks (doc_id, department, chunk_index, content, embedding,
                                           embedding_model, metadata) FROM STDIN""") as copy:
        for index, vector in enumerate(vectors):
            copy.write_row((doc_id, department, index, f"{title} chunk {index}", vector_literal(vector), model,
                            '{"heading_path": ["Section", "Part"]}'))
    return doc_id


@pytest.fixture(scope="module")
def database():
    with throwaway_database() as name:
        with db.connect(name) as conn:
            conn.execute("INSERT INTO departments (name) VALUES ('Beauty'), ('Fresh Grocery')")
            conn.execute("INSERT INTO users (name, department_id, role) VALUES ('Test Staff', 1, 'staff')")
            add_document(conn, "Beauty", "Beauty SOP", [near(0.9), near(0.5)])
            add_document(conn, "Fresh Grocery", "Grocery SOP", [near(0.95)])
            add_document(conn, "ALL", "Company SOP", [near(0.7)])
            add_document(conn, "Beauty", "Older model", [near(0.99)], model="another-embedding-model")
        yield name


@pytest.fixture
async def pool(database):
    async with db.query_pool(database) as opened:
        await opened.wait()   # opening only starts connecting; until this, a test could kill nothing
        yield opened


async def titles(pool, departments, top_k=10):
    async with pool.connection() as conn:
        return [chunk.title for chunk in await search(conn, QUESTION, departments, MODEL, top_k)]


@pytest.mark.parametrize("role", ["staff", "manager"])
def test_staff_and_managers_see_their_department_and_company_wide_documents(role):
    assert visible_departments(role, "Beauty") == ["Beauty", "ALL"]


@pytest.mark.parametrize("role", ["executive", "admin"])
def test_executive_and_admin_see_every_department(role):
    assert visible_departments(role, None) is None


@pytest.mark.parametrize(("role", "department"), [("staff", None), ("staff", ""), ("manager", None), ("intern", "Beauty"),
                                                  ("", None), ("ADMIN", None)])
def test_anything_else_fails_closed_instead_of_searching_everything(role, department):
    with pytest.raises(ValueError):
        visible_departments(role, department)


@pytest.mark.anyio
async def test_staff_can_not_retrieve_another_departments_documents(pool):
    found = await titles(pool, visible_departments("staff", "Beauty"))
    assert "Grocery SOP" not in found      # even though it is the closest one
    assert set(found) == {"Beauty SOP", "Company SOP"}


@pytest.mark.anyio
async def test_no_filter_finds_every_department(pool):
    assert set(await titles(pool, None)) == {"Beauty SOP", "Grocery SOP", "Company SOP"}


@pytest.mark.anyio
async def test_the_closest_chunks_come_first_with_their_real_similarity(pool):
    async with pool.connection() as conn:
        chunks = await search(conn, QUESTION, ["Beauty", "ALL"], MODEL, 10)
    assert [chunk.title for chunk in chunks] == ["Beauty SOP", "Company SOP", "Beauty SOP"]
    assert [round(chunk.score, 6) for chunk in chunks] == [0.9, 0.7, 0.5]


@pytest.mark.anyio
async def test_a_chunk_comes_with_what_a_citation_needs(pool):
    async with pool.connection() as conn:
        chunk = (await search(conn, QUESTION, ["Beauty"], MODEL, 1))[0]
    assert (chunk.title, chunk.doc_type, chunk.department, chunk.heading_path) == ("Beauty SOP", "sop", "Beauty", ("Section", "Part"))
    assert chunk.content == "Beauty SOP chunk 0"


@pytest.mark.anyio
async def test_top_k_limits_the_result(pool):
    assert len(await titles(pool, None, top_k=2)) == 2


@pytest.mark.anyio
async def test_chunks_of_another_embedding_model_are_not_compared(pool):
    assert "Older model" not in await titles(pool, ["Beauty"])


@pytest.mark.anyio
async def test_a_department_with_nothing_gets_nothing_rather_than_an_error(pool):
    assert await titles(pool, ["Home Goods"]) == []


@pytest.mark.anyio
async def test_the_scan_setting_ends_with_the_search_and_does_not_reach_the_next_user(pool):
    async with pool.connection() as conn:
        await search(conn, QUESTION, ["Beauty"], MODEL, 1)
        setting = await (await conn.execute("SHOW hnsw.iterative_scan")).fetchone()
    assert setting == ("off",)


# ---------------------------------------------------------------------------
# The approximate index
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def skewed_database():
    """300 chunks of a big department right next to the question, and 2 of a small one far from it: the first
    candidates the index looks at all belong to the big department."""
    rng = random.Random(42)
    with throwaway_database() as name:
        with db.connect(name) as conn:
            conn.execute("INSERT INTO departments (name) VALUES ('Big'), ('Small')")
            conn.execute("INSERT INTO users (name, department_id, role) VALUES ('Test Staff', 1, 'staff')")
            noise = [[1.0] + [rng.gauss(0, 0.01) for _ in range(EMBEDDING_DIMENSIONS - 1)] for _ in range(300)]
            add_document(conn, "Big", "Big SOP", noise)
            add_document(conn, "Small", "Small SOP", [near(0.05), near(0.04)])
            conn.execute("DROP INDEX idx_doc_chunks_department")   # leaves the vector index as the only way in
        yield name


@pytest.mark.anyio
async def test_a_small_department_gets_all_its_chunks_through_the_approximate_index(skewed_database):
    conninfo = psycopg.conninfo.make_conninfo(host=settings.postgres_host, port=settings.postgres_port, dbname=skewed_database,
                                              user=settings.postgres_user, password=settings.postgres_password)
    async with await psycopg.AsyncConnection.connect(conninfo, autocommit=True) as conn:
        await conn.execute("SET enable_seqscan = off")   # as on a table large enough for the planner to use the index
        params = {"vector": vector_literal(QUESTION), "model": MODEL, "top_k": 5, "departments": ["Small", "ALL"]}

        plan = await (await conn.execute("EXPLAIN " + search_sql(filtered=True), params)).fetchall()
        assert "idx_doc_chunks_embedding" in "\n".join(row[0] for row in plan)

        # The trap, shown: with the default setting the same search silently finds nothing
        without_the_setting = await (await conn.execute(search_sql(filtered=True), params)).fetchall()
        assert without_the_setting == []

        chunks = await search(conn, QUESTION, ["Small", "ALL"], MODEL, 5)
    assert [chunk.content for chunk in chunks] == ["Small SOP chunk 0", "Small SOP chunk 1"]


# ---------------------------------------------------------------------------
# The pool
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_the_query_connections_can_not_write(pool):
    async with pool.connection() as conn:
        with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
            await conn.execute("DELETE FROM documents")


@pytest.mark.anyio
async def test_the_query_connections_have_a_short_statement_limit(pool):
    async with pool.connection() as conn:
        assert await (await conn.execute("SHOW statement_timeout")).fetchone() == ("5s",)


@pytest.mark.anyio
async def test_a_cancelled_query_stops_at_once_and_the_pool_still_works(pool):
    started = time.perf_counter()
    with pytest.raises(TimeoutError):
        async with asyncio.timeout(0.2):
            async with pool.connection() as conn:
                await conn.execute("SELECT pg_sleep(4)")
    assert time.perf_counter() - started < 2
    assert await titles(pool, None, top_k=1) == ["Grocery SOP"]


@pytest.mark.anyio
async def test_the_pool_replaces_connections_that_died(pool, database):
    with db.connect() as admin:
        admin.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = %s", (database,))
        # Terminating only sends a signal: wait until they are really gone, or this proves nothing
        deadline = time.time() + 5
        while admin.execute("SELECT count(*) FROM pg_stat_activity WHERE datname = %s", (database,)).fetchone()[0] and time.time() < deadline:
            time.sleep(0.02)
    # More at once than there were connections, so a dead one can't be missed. Without the check on checkout, measured,
    # as many queries fail as there were connections. With it none do, the first ones waiting 0.07 s for a new connection
    results = await asyncio.gather(*(titles(pool, None, top_k=1) for _ in range(db.QUERY_POOL_MIN_SIZE + 2)))
    assert results == [["Grocery SOP"]] * (db.QUERY_POOL_MIN_SIZE + 2)
