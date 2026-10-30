-- ============================================================================
--  V3 -- upload records, role changes, department on chunks
--
--    1. users       new executive role; executive and admin belong to no department
--    2. uploads     one row per upload: who, how far it got, what it replaces, who approved
--    3. documents   upload_id + natural-key unique constraints ("the same document")
--    4. doc_chunks  redundant department (kept consistent by a composite FK) + embedding_model
--    5. drop doc_ingest_status (replaced by uploads)
--
--  Roles and approval rules: docs/permission-model.md.
--  Flyway runs the whole file in one transaction: if any statement fails, every
--  earlier change in this file is rolled back and the schema is never half-migrated.
-- ============================================================================

-- ----------------------------------------------------------------------------
--  1. users
-- ----------------------------------------------------------------------------
ALTER TABLE users DROP CONSTRAINT users_role_check;
ALTER TABLE users ADD CONSTRAINT users_role_check
    CHECK (role IN ('staff', 'manager', 'executive', 'admin'));

-- executive (founder/CEO) and admin (system administrator) belong to no ops
-- department; staff and manager must have one.
-- The order matters: drop NOT NULL and clear existing admins' departments first,
-- then add the constraint. The other way round, existing admin rows violate the
-- new constraint and ADD CONSTRAINT fails.
ALTER TABLE users ALTER COLUMN department_id DROP NOT NULL;
UPDATE users SET department_id = NULL WHERE role = 'admin';
ALTER TABLE users ADD CONSTRAINT chk_users_department_by_role
    CHECK ((department_id IS NULL) = (role IN ('executive', 'admin')));

-- ----------------------------------------------------------------------------
--  2. uploads -- one row per upload
--
--  The backend writes this row before publishing to Kafka, and the consumer
--  updates its status. So the status is queryable right after the upload, and
--  "still queued" is distinguishable from "never received".
--
--  Statuses:
--    no approval needed:                    received -> processing -> done / failed
--    approval needed:   pending_approval --approve--> received -> processing -> done / failed
--                                        --reject---> rejected
--
--  replaces_upload_id: the upload that produced the current version this upload
--  wants to replace; NULL means a new document. When the replacement actually
--  runs, it checks again that this is still the current version. If not, someone
--  else replaced it while the request waited for approval, and the request is void
--  (optimistic concurrency). Versions are identified by upload, not doc_id, because
--  data-table uploads produce no documents row; both pipelines share one rule.
-- ----------------------------------------------------------------------------
CREATE TABLE uploads (
    upload_id           UUID PRIMARY KEY,
    source              TEXT NOT NULL CHECK (source IN ('manual', 'auto')),
    uploaded_by         BIGINT REFERENCES users(user_id),
    content_kind        TEXT NOT NULL CHECK (content_kind IN ('document', 'data_table')),
    doc_type            TEXT CHECK (doc_type IN ('sop', 'campaign_plan', 'campaign_review')),
    table_type          TEXT CHECK (table_type IN ('campaign_products', 'campaign_customer_behavior')),
    department          TEXT NOT NULL,   -- department name, or 'ALL' (sop only); same values as documents.department
    campaign_id         BIGINT REFERENCES campaigns(campaign_id),
    title               TEXT,            -- required for sop (part of its natural key); campaign document titles are generated
    original_filename   TEXT NOT NULL,
    storage_key         TEXT NOT NULL UNIQUE,
    replaces_upload_id  UUID REFERENCES uploads(upload_id),
    status              TEXT NOT NULL CHECK (status IN
                            ('pending_approval', 'received', 'processing', 'done', 'failed', 'rejected')),
    error               TEXT,
    reviewed_by         BIGINT REFERENCES users(user_id),
    reviewed_at         TIMESTAMPTZ,
    review_comment      TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- A manual upload must record who uploaded it; an automatic sync (source = auto) has no uploader
    CONSTRAINT chk_uploads_uploader CHECK ((source = 'manual') = (uploaded_by IS NOT NULL)),
    -- Four-eyes principle: nobody approves their own upload.
    -- If either side is NULL, <> yields NULL; CHECK rejects only FALSE and lets NULL
    -- through, so "not reviewed yet" is unaffected.
    CONSTRAINT chk_uploads_not_self_review CHECK (reviewed_by <> uploaded_by),
    -- Reviewer and review time are both set or both empty
    CONSTRAINT chk_uploads_review_pair CHECK ((reviewed_by IS NULL) = (reviewed_at IS NULL)),
    -- Same combination rules as the message contract: documents need doc_type and only
    -- sop has no campaign; data tables need table_type and a campaign
    CONSTRAINT chk_uploads_kind_fields CHECK (
        (content_kind = 'document' AND doc_type IS NOT NULL AND table_type IS NULL
            AND (doc_type = 'sop') = (campaign_id IS NULL)) OR
        (content_kind = 'data_table' AND table_type IS NOT NULL AND doc_type IS NULL
            AND campaign_id IS NOT NULL)
    ),
    CONSTRAINT chk_uploads_sop_title CHECK (doc_type IS DISTINCT FROM 'sop' OR title IS NOT NULL),
    CONSTRAINT chk_uploads_all_only_sop CHECK (department <> 'ALL' OR doc_type = 'sop')
);

-- ----------------------------------------------------------------------------
--  3. documents
-- ----------------------------------------------------------------------------
-- The upload that produced the current version. A replacement updates this row in
-- place and keeps doc_id: only upload_id switches to the new upload and all chunks
-- are rebuilt. With a stable doc_id, citations in answers and doc_ids labelled in
-- the eval set stay valid when a document gets a new version.
-- The table is empty, so NOT NULL can be added directly; on a table with data this
-- takes three steps: add a nullable column -> backfill -> set NOT NULL.
ALTER TABLE documents ADD COLUMN upload_id UUID NOT NULL UNIQUE REFERENCES uploads(upload_id);

-- Natural key 1: plans and reviews -- one of each per campaign.
-- campaign_id is NULL for sop, and a unique constraint treats NULLs as distinct,
-- so sop rows never collide here; in effect it only covers campaign documents.
ALTER TABLE documents ADD CONSTRAINT uq_documents_campaign_doc UNIQUE (campaign_id, doc_type);
-- The unique constraint brings its own index starting with campaign_id, so the old
-- single-column index is redundant
DROP INDEX idx_documents_campaign;

-- Natural key 2: SOP -- titles are unique within a department (or ALL).
-- Duplicates are detected on the normalized title: NFKC folds full-width letters,
-- digits and spaces to their ASCII forms, btrim strips surrounding spaces, lower
-- ignores case. The table keeps the original title; only the uniqueness check
-- uses the normalized value.
CREATE UNIQUE INDEX uq_documents_sop_title ON documents
    (department, lower(btrim(normalize(title, NFKC))))
    WHERE doc_type = 'sop';

-- 'ALL' is for sop only: a campaign document always belongs to one department
ALTER TABLE documents ADD CONSTRAINT chk_documents_all_only_sop
    CHECK (department <> 'ALL' OR doc_type = 'sop');

-- The referenced side of the composite FK from doc_chunks. doc_id is already unique,
-- so (doc_id, department) is unique too and this constraint restricts nothing; a
-- foreign key just requires a unique constraint on the referenced column set.
ALTER TABLE documents ADD CONSTRAINT uq_documents_id_department UNIQUE (doc_id, department);

-- ----------------------------------------------------------------------------
--  4. doc_chunks
-- ----------------------------------------------------------------------------
-- department: deliberately redundant (documents already has it). Retrieval filters
-- on this column without a JOIN; filtered vector search and later RLS depend on it.
-- It was first planned as a key inside the metadata JSONB and became a real column
-- so it can carry constraints and an index.
-- The risk of redundancy is the two copies disagreeing, so a composite FK makes the
-- database enforce consistency:
--   (doc_id, department) must exist in documents -> a chunk's department can only
--     be its document's department
--   ON UPDATE CASCADE: when a document changes department (say a replacement makes
--     it ALL), all its chunks follow
--   ON DELETE CASCADE: deleting a document deletes its chunks, as the old FK did
-- The composite FK covers what the old single-column FK on doc_id did, so that one goes.
-- doc_id becomes NOT NULL: if any column of a foreign key is NULL, the database skips
-- the check and consistency could be bypassed.
ALTER TABLE doc_chunks DROP CONSTRAINT doc_chunks_doc_id_fkey;
ALTER TABLE doc_chunks ALTER COLUMN doc_id SET NOT NULL;
ALTER TABLE doc_chunks ADD COLUMN department TEXT NOT NULL;
ALTER TABLE doc_chunks ADD CONSTRAINT fk_doc_chunks_document
    FOREIGN KEY (doc_id, department) REFERENCES documents (doc_id, department)
    ON UPDATE CASCADE ON DELETE CASCADE;

-- Chunk numbers are unique within a document. The index starts with doc_id, which
-- also serves the cascade when a document is deleted
ALTER TABLE doc_chunks ADD CONSTRAINT uq_doc_chunks_doc_index UNIQUE (doc_id, chunk_index);
-- When retrieval for a small department uses an exact scan, filter by department first
CREATE INDEX idx_doc_chunks_department ON doc_chunks (department);

-- The embedding model that produced the vector. The embedding column is fixed at
-- 1536 dimensions, so this column only tells apart models of that dimension; a model
-- with another dimension needs another column or table.
ALTER TABLE doc_chunks ADD COLUMN embedding_model TEXT NOT NULL;

-- ----------------------------------------------------------------------------
--  5. doc_ingest_status is replaced by uploads
--  Its primary key was doc_id: there is no doc_id before the document is written,
--  and a data-table upload never produces a document, so it could record neither case.
-- ----------------------------------------------------------------------------
DROP TABLE doc_ingest_status;
