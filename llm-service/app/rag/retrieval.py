"""Finding the chunks that may answer a question, among those the asker is allowed to see.

The department filter is part of the search itself, never a step after it, and it fails closed: a role this
code doesn't know, or staff without a department, is an error and not an unfiltered search.

Why the iterative scan: HNSW is an approximate index. It takes about 40 nearest candidates first and applies
the WHERE clause to those, so a small department whose chunks aren't among them silently gets too few results,
with no error, and documents the user may see simply don't show up. With `hnsw.iterative_scan` the index keeps
going until LIMIT is met. `relaxed_order` is what makes it finish early, at the price of slightly out-of-order
results, so the outer query sorts them again. The planner still picks an exact scan by itself when the filter
keeps few rows, which needs no code here.
"""
from collections.abc import Sequence
from dataclasses import dataclass

import psycopg

from app.rag.store import vector_literal

ALL_DEPARTMENTS = "ALL"
ROLES_LIMITED_TO_A_DEPARTMENT = ("staff", "manager")
ROLES_THAT_SEE_EVERYTHING = ("executive", "admin")

_SEARCH = """
    WITH candidates AS MATERIALIZED (
        SELECT chunk_id, doc_id, department, content, metadata, embedding <=> %(vector)s::vector AS distance
        FROM doc_chunks
        WHERE embedding_model = %(model)s {department_filter}
        ORDER BY distance
        LIMIT %(top_k)s
    )
    SELECT c.chunk_id, c.doc_id, d.doc_type, d.title, c.department, c.metadata, c.content, 1 - c.distance
    FROM candidates c JOIN documents d USING (doc_id)
    ORDER BY c.distance, c.chunk_id
"""
_ONLY_THESE_DEPARTMENTS = "AND department = ANY(%(departments)s)"


@dataclass(frozen=True)
class RetrievedChunk:
    chunk_id: int
    doc_id: int
    doc_type: str
    title: str
    department: str
    heading_path: tuple[str, ...]
    content: str
    score: float   # cosine similarity: 1 is the same direction, 0 unrelated


def visible_departments(role: str, department: str | None) -> list[str] | None:
    """The departments a user may read documents from; None means all of them."""
    if role in ROLES_THAT_SEE_EVERYTHING:
        return None
    if role in ROLES_LIMITED_TO_A_DEPARTMENT and department:
        return [department, ALL_DEPARTMENTS]
    raise ValueError(f"no document access can be derived for role={role!r}, department={department!r}")


def search_sql(*, filtered: bool) -> str:
    return _SEARCH.format(department_filter=_ONLY_THESE_DEPARTMENTS if filtered else "")


async def search(conn: psycopg.AsyncConnection, vector: Sequence[float], departments: list[str] | None,
                 embedding_model: str, top_k: int) -> list[RetrievedChunk]:
    """The closest chunks, best first. Only chunks embedded by the same model: comparing vectors across models
    has no meaning, even at the same dimension."""
    params = {"vector": vector_literal(vector), "model": embedding_model, "top_k": top_k, "departments": departments}
    # SET LOCAL lasts until the end of the transaction, so it can't leak to the next user of a pooled connection
    async with conn.transaction():
        await conn.execute("SET LOCAL hnsw.iterative_scan = relaxed_order")
        cursor = await conn.execute(search_sql(filtered=departments is not None), params)
        rows = await cursor.fetchall()
    return [RetrievedChunk(chunk_id=row[0], doc_id=row[1], doc_type=row[2], title=row[3], department=row[4],
                           heading_path=tuple(row[5].get("heading_path", [])), content=row[6], score=float(row[7]))
            for row in rows]
