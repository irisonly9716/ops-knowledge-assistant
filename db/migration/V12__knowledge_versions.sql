-- V12: the version of what a question can be answered from, for the answer cache's key.
--
-- An answer is cached under the versions of the knowledge it came from, and every write of that knowledge moves them
-- on. A cached answer is never deleted: after a write its key is simply never asked for again, and Redis lets it
-- expire. Three scopes, because of who sees what:
--   department   one row per department: its documents and its campaign data
--   all          documents for every department (department 'ALL')
--   everything   moved on by every write; an executive's or admin's answer can rest on any department
-- A staff member's or a manager's key holds their department's version and 'all'; an executive's or admin's,
-- 'everything'.
--
-- In Postgres, in the transaction that writes the document or the table: kept in Redis, the version would be a second
-- write after the commit, and a crash between the two would keep serving the old answers until they expire, the dual
-- write the outbox (V6) removed from uploads.
--
-- The values come from one sequence, so a version is never handed out twice: if the table is emptied (mock data
-- resets departments with TRUNCATE ... CASCADE), a key cached before can't match an answer written after. A scope with
-- no row has never been written to and reads as 0.

CREATE SEQUENCE knowledge_version_seq;

CREATE TABLE knowledge_versions (
    knowledge_version_id SERIAL PRIMARY KEY,
    scope                TEXT NOT NULL CHECK (scope IN ('department', 'all', 'everything')),
    department_id        INT REFERENCES departments(department_id) ON DELETE CASCADE,
    version              BIGINT NOT NULL,
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT chk_knowledge_versions_department CHECK ((scope = 'department') = (department_id IS NOT NULL)),
    -- NULLS NOT DISTINCT: 'all' and 'everything' have no department, and must still be one row each
    CONSTRAINT uq_knowledge_versions_scope UNIQUE NULLS NOT DISTINCT (scope, department_id)
);

COMMENT ON TABLE knowledge_versions IS
    'The version of the knowledge each scope answers from, moved on in the transaction of every document or table write. Part of the answer cache''s key.';
COMMENT ON COLUMN knowledge_versions.version IS 'From knowledge_version_seq: never reused, even after the table is emptied.';
