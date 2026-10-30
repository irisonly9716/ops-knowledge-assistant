-- ============================================================================
--  V2 -- base schema (described in docs/data-schema.md)
--  Runs after V1, so the vector type is available. Later schema changes live in
--  V3 and up; this file is not edited again.
--
--  Primary keys are BIGSERIAL (64-bit identity) to match the backend-service
--  entities (Long + GenerationType.IDENTITY). Hibernate's ddl-auto: validate
--  checks entities against the tables and refuses to start on a type mismatch.
-- ============================================================================

-- ----------------------------------------------------------------------------
--  departments -- the unit of the permission model
-- ----------------------------------------------------------------------------
CREATE TABLE departments (
    department_id   BIGSERIAL PRIMARY KEY,
    name            TEXT NOT NULL UNIQUE
);

-- ----------------------------------------------------------------------------
--  users -- employees. department_id + role drive row-level filtering
--  (see docs/permission-model.md). There is no real login: requests carry an
--  X-User-Id header that maps to user_id here.
--
--  There is no "host team" level below departments. It was considered and left
--  out: department-level permissions come first.
-- ----------------------------------------------------------------------------
CREATE TABLE users (
    user_id         BIGSERIAL PRIMARY KEY,
    name            TEXT NOT NULL,
    department_id   BIGINT NOT NULL REFERENCES departments(department_id),
    role            TEXT NOT NULL CHECK (role IN ('staff', 'manager', 'admin'))
);
CREATE INDEX idx_users_department ON users(department_id);

-- ----------------------------------------------------------------------------
--  hosts -- livestream hosts
-- ----------------------------------------------------------------------------
CREATE TABLE hosts (
    host_id         BIGSERIAL PRIMARY KEY,
    name            TEXT NOT NULL,
    department_id   BIGINT NOT NULL REFERENCES departments(department_id),
    category        TEXT,
    fans_count      INTEGER DEFAULT 0,
    join_date       DATE
);
CREATE INDEX idx_hosts_department ON hosts(department_id);

-- ----------------------------------------------------------------------------
--  campaigns -- department_id is the field permission filtering keys on
--  (simplification: one owning department per campaign, see section 4 of
--  docs/permission-model.md)
--
--  host_id: one lead host per campaign (real campaigns can have co-hosts).
--  Plans and reviews trace back to their host through this table
--  (documents.campaign_id -> campaigns.host_id -> hosts), so documents does
--  not store a second copy of host_id.
-- ----------------------------------------------------------------------------
CREATE TABLE campaigns (
    campaign_id     BIGSERIAL PRIMARY KEY,
    name            TEXT NOT NULL,
    department_id   BIGINT NOT NULL REFERENCES departments(department_id),
    host_id         BIGINT NOT NULL REFERENCES hosts(host_id),
    category        TEXT,
    start_date      DATE NOT NULL,
    end_date        DATE NOT NULL,
    status          TEXT NOT NULL CHECK (status IN ('planned', 'running', 'finished')),
    -- A shortcut, not the source of truth: the truth is whether documents has a
    -- row with this campaign_id and doc_type. These flags let the router skip a
    -- vector search with one cheap query when there is no plan or review at all.
    -- Cost: ingestion must update them whenever it inserts or deletes documents,
    -- or they drift from the documents table. The redundancy is deliberate.
    has_plan_doc    BOOLEAN NOT NULL DEFAULT FALSE,
    has_review_doc  BOOLEAN NOT NULL DEFAULT FALSE,
    -- Operating cost (placement fees + promotion + giveaways; product cost is in
    -- campaign_products). "planned" is the budget; "actual" can only be filled once
    -- the campaign is over and all invoices are in, so it may be NULL.
    planned_operating_cost   NUMERIC(14, 2),
    actual_operating_cost    NUMERIC(14, 2)
);
CREATE INDEX idx_campaigns_department ON campaigns(department_id);
CREATE INDEX idx_campaigns_host ON campaigns(host_id);

-- ----------------------------------------------------------------------------
--  campaign_metrics -- daily campaign metrics
--  Only clean numbers we can get directly. ROI, per-product breakdowns and
--  root-cause analysis need judgment; they are content and go through documents
--  (campaign_review) and RAG, not through structured queries.
-- ----------------------------------------------------------------------------
CREATE TABLE campaign_metrics (
    id                          BIGSERIAL PRIMARY KEY,
    campaign_id                 BIGINT NOT NULL REFERENCES campaigns(campaign_id) ON DELETE CASCADE,
    date                        DATE NOT NULL,
    gmv                         NUMERIC(14, 2),
    dau                         INTEGER,
    watch_time                  NUMERIC(10, 2),
    conversion_rate             NUMERIC(6, 4),
    peak_concurrent_viewers     INTEGER,        -- peak concurrent viewers that day
    avg_dwell_time_minutes      NUMERIC(6, 2),  -- average minutes a viewer stayed that day
    UNIQUE (campaign_id, date) -- at most one metrics row per campaign per day
);
CREATE INDEX idx_metrics_campaign ON campaign_metrics(campaign_id);

-- ----------------------------------------------------------------------------
--  campaign_products -- per-product sales data, uploaded by ops (an Excel/CSV
--  export), not computed by us. This is the structured-table pipeline, separate
--  from documents below: documents are free text that gets chunked and embedded
--  for RAG; this table and campaign_customer_behavior are parsed straight into
--  rows and queried with Text-to-SQL. See docs/architecture.md.
-- ----------------------------------------------------------------------------
CREATE TABLE campaign_products (
    id                  BIGSERIAL PRIMARY KEY,
    campaign_id         BIGINT NOT NULL REFERENCES campaigns(campaign_id) ON DELETE CASCADE,
    product_name        TEXT NOT NULL,
    units_sold          INTEGER,
    sell_through_rate   NUMERIC(5, 4),
    return_rate         NUMERIC(5, 4),
    sales_amount        NUMERIC(14, 2),
    click_through_rate  NUMERIC(5, 4),
    cost_price          NUMERIC(10, 2), -- unit cost
    profit              NUMERIC(14, 2), -- total profit of this product in this campaign
    profit_margin       NUMERIC(5, 4),
    UNIQUE (campaign_id, product_name)
);
CREATE INDEX idx_products_campaign ON campaign_products(campaign_id);

-- ----------------------------------------------------------------------------
--  campaign_customer_behavior -- customer behavior data, also uploaded by ops.
--  customer_ref is an anonymized ID (no names or phone numbers). Repeat-purchase
--  rate needs no order system: group by customer_ref and count the distinct
--  campaigns a customer appears in. One plain SQL query.
-- ----------------------------------------------------------------------------
CREATE TABLE campaign_customer_behavior (
    id                      BIGSERIAL PRIMARY KEY,
    campaign_id             BIGINT NOT NULL REFERENCES campaigns(campaign_id) ON DELETE CASCADE,
    customer_ref            TEXT NOT NULL,
    region                  TEXT,
    dwell_time_minutes      NUMERIC(6, 2),
    products_purchased      JSONB DEFAULT '[]'::jsonb, -- a customer may buy several products
    purchase_amount         NUMERIC(10, 2),
    ip_address              TEXT
);
CREATE INDEX idx_behavior_campaign ON campaign_customer_behavior(campaign_id);
CREATE INDEX idx_behavior_customer ON campaign_customer_behavior(customer_ref); -- finds one customer across campaigns

-- ----------------------------------------------------------------------------
--  documents -- one table for three document types:
--    sop             operating procedures and guidelines
--    campaign_plan   livestream plan, written before the campaign; AI later
--                    compares it with the results ("which choices led to what")
--    campaign_review post-campaign review, including ROI and other results.
--                    ROI is document content, not a field we compute and store.
--
--  All three go through the same ingestion pipeline (upload -> Kafka -> parse ->
--  chunk -> embed), hence one table with doc_type instead of three tables and
--  three copies of the ingestion logic.
--
--  department is TEXT, not a foreign key, so it can hold the sentinel 'ALL'
--  ("every department"). Only sop uses 'ALL'; plans and reviews always belong to
--  one department, the same as their campaign's.
-- ----------------------------------------------------------------------------
CREATE TABLE documents (
    doc_id              BIGSERIAL PRIMARY KEY,
    doc_type            TEXT NOT NULL CHECK (doc_type IN ('sop', 'campaign_plan', 'campaign_review')),
    title               TEXT NOT NULL,
    department          TEXT NOT NULL, -- department name, or 'ALL' (sop only)
    campaign_id         BIGINT REFERENCES campaigns(campaign_id), -- plans and reviews only
    category            TEXT,
    steps               JSONB NOT NULL DEFAULT '[]'::jsonb, -- structured steps, mainly for sop
    responsible_team    TEXT,
    source_path         TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- An SOP never belongs to a campaign; plans and reviews always do. The CHECK
    -- pins this business rule in the database instead of trusting application code.
    CONSTRAINT chk_document_campaign_link CHECK (
        (doc_type = 'sop' AND campaign_id IS NULL) OR
        (doc_type != 'sop' AND campaign_id IS NOT NULL)
    )
);
CREATE INDEX idx_documents_campaign ON documents(campaign_id);

-- ----------------------------------------------------------------------------
--  doc_chunks -- document chunks + vectors, written by the ingestion pipeline.
--  metadata holds per-chunk details such as the heading path.
-- ----------------------------------------------------------------------------
CREATE TABLE doc_chunks (
    chunk_id        BIGSERIAL PRIMARY KEY,
    doc_id          BIGINT REFERENCES documents(doc_id) ON DELETE CASCADE,
    chunk_index     INTEGER NOT NULL,
    content         TEXT NOT NULL,
    embedding       VECTOR(1536), -- dimension of text-embedding-3-small
    metadata        JSONB NOT NULL DEFAULT '{}'::jsonb
);
-- HNSW: approximate nearest-neighbor index for similarity search. Creating it on
-- an empty table is fine and saves a schema change once data arrives.
CREATE INDEX idx_doc_chunks_embedding ON doc_chunks
    USING hnsw (embedding vector_cosine_ops);

-- ----------------------------------------------------------------------------
--  doc_ingest_status -- document processing status (replaced by uploads in V3)
-- ----------------------------------------------------------------------------
CREATE TABLE doc_ingest_status (
    doc_id          BIGINT PRIMARY KEY REFERENCES documents(doc_id) ON DELETE CASCADE,
    status          TEXT NOT NULL CHECK (status IN ('uploading', 'processing', 'done', 'failed')),
    error           TEXT,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
