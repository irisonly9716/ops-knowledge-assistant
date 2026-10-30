-- ============================================================================
--  V1 -- enable the pgvector extension (the vector type and similarity search)
--
--  Flyway runs the files in db/migration/ in version order (see the flyway
--  service in docker-compose.yml). An applied migration must never be edited:
--  Flyway stores each file's checksum and the next migrate fails if it changed.
--  To change the schema, add a file with a higher version number.
-- ============================================================================

CREATE EXTENSION IF NOT EXISTS vector;
