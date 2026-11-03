-- ============================================================================
--  V8 -- two counts on uploads that have to survive a process dying
--
--  delivery_count: how many times the consumer started on this upload without finishing it, written before any
--  work starts and reset when it ends in done or failed. Retries used to be counted only in memory, so a file that
--  killed the process came back with a fresh count after every restart, never reached the dead-letter topic, and
--  on one partition blocked everything behind it.
--
--  republish_count / last_republished_at: how often the reconciliation job published an upload's event again
--  because its message was gone, so it stops after a limit instead of trying forever.
-- ============================================================================

ALTER TABLE uploads ADD COLUMN delivery_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE uploads ADD COLUMN republish_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE uploads ADD COLUMN last_republished_at TIMESTAMPTZ;
