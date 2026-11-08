-- ============================================================================
--  V10 -- example questions with their SQL, for the Text-to-SQL pipeline
--
--  For each question the pipeline shows a model the few examples closest to it (few-shot): how this database is
--  queried for a kind of question, such as ROI from campaign_summary or a ranking that leaves out campaigns still
--  running. The examples are kept in llm-service/app/sql_agent/examples.yaml and loaded with their embeddings by
--  scripts.load_sql_examples; this table is what the query path searches.
--
--  department: a department name, or 'ALL' for every department, as for documents. Retrieval keeps to the asker's
--  department and ALL, so a department can have examples of its own. The examples for ALL name no real campaign or
--  product: everyone is shown them.
--
--  No vector index: with tens of rows an exact scan is faster than an index and never misses one.
-- ============================================================================
CREATE TABLE sql_examples (
    example_id      BIGSERIAL PRIMARY KEY,
    department      TEXT NOT NULL,   -- department name, or 'ALL'
    question        TEXT NOT NULL,
    sql             TEXT NOT NULL,
    embedding       VECTOR(1536) NOT NULL,   -- of the question, by embedding_model
    embedding_model TEXT NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_sql_examples_question UNIQUE (department, question)
);

COMMENT ON TABLE sql_examples IS 'Example questions and the SQL that answers them, shown to the model that writes SQL. Loaded from llm-service/app/sql_agent/examples.yaml.';
