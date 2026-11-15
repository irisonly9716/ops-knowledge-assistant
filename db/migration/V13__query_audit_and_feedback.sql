-- V13: a row for every question asked, and what its asker thought of the answer.
--
-- query_audit is written by llm-service when a question is done, whatever became of it: answered, refused, asked back,
-- or failed on the way (app/audit.py). It holds who asked, what ran, what each step took and what the model calls
-- cost, so a wrong answer can be traced and the query path sized from real traffic (#11). A row is written once and
-- never changed, except that the text of the question, its rewrite and its SQL is cleared after the retention period:
-- what people ask is private, and the numbers are what is kept. The backend's AuditRetention clears them.
--
-- query_feedback is the asker's thumbs up or down on one answer, written by the backend. A table of its own, because
-- feedback changes when the asker changes their mind and the audit row must not.
--
-- Only admin reads either, through the backend (docs/permission-model.md). sql_reader is granted neither: it reads the
-- metric views and nothing else (V11).
--
-- user_id and campaign_id are foreign keys, so a user or a campaign that questions were asked about can't be deleted
-- out from under its history. Mock data empties users and campaigns with TRUNCATE ... CASCADE, and with them these.

CREATE TABLE query_audit (
    query_id          UUID PRIMARY KEY,                  -- made by llm-service, returned with the answer as queryId
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    user_id           BIGINT NOT NULL REFERENCES users(user_id),
    role              TEXT NOT NULL CHECK (role IN ('staff', 'manager', 'executive', 'admin')),
    department        TEXT,                              -- the asker's, by name; NULL for executive and admin
    -- Executives and admins read every department's documents and data: their questions are the ones to look at first
    cross_department  BOOLEAN GENERATED ALWAYS AS (role IN ('executive', 'admin')) STORED,
    session_id        UUID,                              -- the conversation; NULL for a question on its own
    -- answered, refused, failed, clarify: the answer's status. The others: no answer came back
    outcome           TEXT NOT NULL CHECK (outcome IN ('answered', 'refused', 'failed', 'clarify', 'invalid', 'timeout',
                                                       'busy', 'rate_limited', 'model_error', 'database_error', 'error')),
    cache_hit         TEXT CHECK (cache_hit IN ('as_asked', 'rewritten')),   -- NULL: not from the cache
    route             TEXT CHECK (route IN ('documents', 'data', 'both', 'none')),
    decided_by        TEXT CHECK (decided_by IN ('model', 'keywords')),
    confidence        REAL,
    paths             TEXT[] NOT NULL DEFAULT '{}',      -- the paths the answer rests on; a cache hit's, when cached
    cancelled         TEXT[] NOT NULL DEFAULT '{}',      -- paths the route ruled out while they ran
    written_by        TEXT,                              -- documents | data | both | cache
    question          TEXT,                              -- NULL once the retention period is over
    rewritten         TEXT,                              -- the question filled in; NULL when it wasn't rewritten
    sql               TEXT,                              -- the query the answer rests on
    campaign_id       BIGINT REFERENCES campaigns(campaign_id),   -- the campaign "the last one" turned out to be
    sql_attempts      SMALLINT,
    result_rows       INT,
    cited_doc_ids     BIGINT[] NOT NULL DEFAULT '{}',
    -- The model calls that finished and their tokens; a cancelled path's are unknown. NULL when nothing came back
    -- (past the time limit, or an error on the way): what was spent is unknown, not nothing
    model_calls       SMALLINT,
    input_tokens      INT,
    output_tokens     INT,
    tokens            JSONB,                             -- by part of the path: {"router": [input, output], ...}
    cost_usd          NUMERIC(12, 8),                    -- at the prices when it ran; also NULL for a model with no price
    total_ms          INT NOT NULL,
    timings_ms        JSONB                              -- by step, each from the start: {"route": 1210, ...}
);

COMMENT ON TABLE query_audit IS
    'One row per question asked, written by llm-service once it is done. Text columns are cleared after the retention period.';

-- The admin's listing, newest first, of everyone, of one user, or of executives' and admins' questions
CREATE INDEX idx_query_audit_created ON query_audit (created_at DESC);
CREATE INDEX idx_query_audit_user ON query_audit (user_id, created_at DESC);
CREATE INDEX idx_query_audit_cross_department ON query_audit (created_at DESC) WHERE cross_department;
-- The rows whose text is still to be cleared: every question has text when written, so the retention finds them
-- without reading the rows it already cleared
CREATE INDEX idx_query_audit_text_kept ON query_audit (created_at) WHERE question IS NOT NULL;

CREATE TABLE query_feedback (
    query_id    UUID PRIMARY KEY REFERENCES query_audit(query_id) ON DELETE CASCADE,
    rating      TEXT NOT NULL CHECK (rating IN ('up', 'down')),
    comment     TEXT CHECK (char_length(comment) <= 1000),   -- NULL once the retention period is over
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE query_feedback IS
    'The asker''s rating of one answer; a second rating replaces the first. The comment is cleared after the retention period.';

CREATE INDEX idx_query_feedback_comment_kept ON query_feedback (updated_at) WHERE comment IS NOT NULL;
