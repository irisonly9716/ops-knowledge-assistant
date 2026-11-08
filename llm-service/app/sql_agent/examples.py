"""The example questions shown to the model that writes SQL: the few closest to a question (few-shot).

They are kept in examples.yaml, reviewed like code, and loaded with their embeddings into sql_examples (migration V10)
by scripts.load_sql_examples. They are searched like the documents: by the cosine similarity of the question's
embedding, among the asker's department's examples and those for every department (ALL). An exact scan: there are tens.
"""
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import psycopg
import yaml

from app.rag.retrieval import ALL_DEPARTMENTS
from app.rag.store import vector_literal

EXAMPLES_FILE = Path(__file__).with_name("examples.yaml")

_CLOSEST = """
    SELECT question, sql, department FROM sql_examples
    WHERE embedding_model = %(model)s AND (%(departments)s::text[] IS NULL OR department = ANY(%(departments)s))
    ORDER BY embedding <=> %(vector)s::vector, example_id
    LIMIT %(k)s
"""


@dataclass(frozen=True)
class Example:
    question: str
    sql: str
    department: str = ALL_DEPARTMENTS


def read_file(path: Path = EXAMPLES_FILE) -> list[Example]:
    return [Example(entry["question"].strip(), entry["sql"].strip(), entry.get("department", ALL_DEPARTMENTS))
            for entry in yaml.safe_load(path.read_text(encoding="utf-8"))]


async def closest(conn: psycopg.AsyncConnection, vector: Sequence[float], departments: list[str] | None,
                  embedding_model: str, k: int) -> list[Example]:
    """The k examples closest to the question, closest first. departments: the ones the asker may see, None for all.
    Only examples embedded by the same model, as for documents."""
    cursor = await conn.execute(_CLOSEST, {"vector": vector_literal(vector), "model": embedding_model,
                                           "departments": departments, "k": k})
    return [Example(question, sql, department) for question, sql, department in await cursor.fetchall()]


def replace_all(conn: psycopg.Connection, examples: Sequence[Example], vectors: Sequence[Sequence[float]],
                embedding_model: str) -> None:
    """The file is the source: in one transaction the old examples go and these come in, so a search never sees half
    of each."""
    with conn.transaction():
        conn.execute("DELETE FROM sql_examples")
        with conn.cursor() as cursor:
            cursor.executemany(
                "INSERT INTO sql_examples (department, question, sql, embedding, embedding_model) "
                "VALUES (%s, %s, %s, %s::vector, %s)",
                [(e.department, e.question, e.sql, vector_literal(v), embedding_model)
                 for e, v in zip(examples, vectors, strict=True)])
