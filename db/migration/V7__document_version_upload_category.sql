-- ============================================================================
--  V7 -- documents.version and uploads.category
--
--  documents.version: 1 for a new document, +1 every time a replacement goes through, so citations and
--  audit records can say which version an answer came from. doc_id stays the same across versions.
--
--  uploads.category: the form field had nowhere to live but the Kafka message. An upload that needs
--  approval only gets its message once it is approved, possibly days later, and the message is then built
--  from this row, so everything the message carries has to be in the row.
-- ============================================================================

ALTER TABLE documents ADD COLUMN version INTEGER NOT NULL DEFAULT 1;

ALTER TABLE uploads ADD COLUMN category TEXT;
