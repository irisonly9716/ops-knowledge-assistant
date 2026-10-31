-- ============================================================================
--  V6 -- transactional outbox for upload events
--
--  Recording an upload (the uploads row) and publishing its event to Kafka touch two systems with no
--  shared transaction. Done one after the other, a crash in between leaves an upload that is recorded
--  but never published (or the other way round). With the outbox, the backend writes the uploads row
--  and an outbox row holding the serialized event in ONE database transaction: both exist or neither
--  does. A relay in the backend then publishes unpublished rows to Kafka and marks them published.
--
--  The relay can publish a message twice (it crashes after Kafka acknowledged but before the row is
--  marked), so delivery is at least once; the consumer is idempotent on the uploads status.
-- ============================================================================

CREATE TABLE outbox (
    id            BIGSERIAL PRIMARY KEY,      -- also the publishing order
    topic         TEXT NOT NULL,
    message_key   TEXT NOT NULL,              -- the Kafka key (the upload id)
    payload       TEXT NOT NULL,              -- the message exactly as it is published (JSON)
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    published_at  TIMESTAMPTZ,                -- NULL until Kafka has acknowledged the message
    attempts      INTEGER NOT NULL DEFAULT 0, -- failed publish attempts so far
    last_error    TEXT                        -- why the last attempt failed
);

-- The relay only ever looks for unpublished rows, oldest first. A partial index stays as small as
-- the backlog, however many published rows the table holds
CREATE INDEX idx_outbox_unpublished ON outbox (id) WHERE published_at IS NULL;
