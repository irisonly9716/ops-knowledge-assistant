"""Loads the example questions of app/sql_agent/examples.yaml into sql_examples, with their embeddings.

    cd llm-service
    uv run python -m scripts.load_sql_examples

Every example is checked first: it has to pass the checks a generated query passes (app/sql_agent/guard.py) and run on
this database. Only then are the questions embedded (tens of short questions cost a tiny fraction of a cent) and the
table replaced in one transaction, so a bad example never replaces good ones. Run it again after editing the file, and
after scripts.generate_mock_data, which doesn't touch this table but may change what the examples return.
"""
from app import db
from app.rag.embeddings import embed_in_batches, openai_embedder_from_settings
from app.sql_agent import examples, guard


def main() -> None:
    loaded = examples.read_file()
    with db.connect() as conn:
        for example in loaded:
            sql = guard.checked(example.sql, guard.METRIC_VIEWS, one_department=False)
            with conn.transaction():
                conn.execute("SET TRANSACTION READ ONLY")
                conn.execute(sql).fetchall()
        embedder = openai_embedder_from_settings()
        batch = embed_in_batches(embedder, [example.question for example in loaded])
        examples.replace_all(conn, loaded, batch.vectors, embedder.model)
    print(f"{len(loaded)} examples loaded from {examples.EXAMPLES_FILE.name} ({batch.total_tokens} tokens embedded)")


if __name__ == "__main__":
    main()
