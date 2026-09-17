-- ============================================================================
--  02_schema.sql —— 正式建表(对应 docs/data-schema.md)
--  只在 Postgres 容器"第一次创建数据库"时自动跑一次(挂在 /docker-entrypoint-initdb.d,
--  按文件名顺序排在 01_init.sql 后面执行,所以这里能放心用 vector 类型)。
--  改了这个文件想让它重新生效:docker compose down -v 再 up。
--
--  主键都用 BIGSERIAL(64 位自增),是为了跟 backend-service 里 Department 实体
--  用 Long + GenerationType.IDENTITY 的映射对齐——Hibernate 的 ddl-auto: validate
--  模式会检查实体和表结构是否一致,类型对不上会启动报错。
-- ============================================================================

-- ----------------------------------------------------------------------------
--  departments —— 部门,权限模型的基准表
-- ----------------------------------------------------------------------------
CREATE TABLE departments (
    department_id   BIGSERIAL PRIMARY KEY,
    name            TEXT NOT NULL UNIQUE
);

-- ----------------------------------------------------------------------------
--  users —— 员工。department_id + role 是行级权限过滤的依据
--  (见 docs/permission-model.md)。这个项目没有真登录,查询时用请求头
--  X-User-Id 对应这里的 user_id,查出 department_id / role。
-- ----------------------------------------------------------------------------
CREATE TABLE users (
    user_id         BIGSERIAL PRIMARY KEY,
    name            TEXT NOT NULL,
    department_id   BIGINT NOT NULL REFERENCES departments(department_id),
    role            TEXT NOT NULL CHECK (role IN ('staff', 'manager', 'admin'))
);
CREATE INDEX idx_users_department ON users(department_id);

-- ----------------------------------------------------------------------------
--  anchors —— 主播
-- ----------------------------------------------------------------------------
CREATE TABLE anchors (
    anchor_id       BIGSERIAL PRIMARY KEY,
    name            TEXT NOT NULL,
    department_id   BIGINT NOT NULL REFERENCES departments(department_id),
    category        TEXT,
    fans_count      INTEGER DEFAULT 0,
    join_date       DATE
);
CREATE INDEX idx_anchors_department ON anchors(department_id);

-- ----------------------------------------------------------------------------
--  campaigns —— 活动。department_id 是权限过滤的主键字段
--  (简化方案:一个 campaign 只记一个主责部门,见 permission-model.md 第 4 节)
-- ----------------------------------------------------------------------------
CREATE TABLE campaigns (
    campaign_id     BIGSERIAL PRIMARY KEY,
    name            TEXT NOT NULL,
    department_id   BIGINT NOT NULL REFERENCES departments(department_id),
    category        TEXT,
    start_date      DATE NOT NULL,
    end_date        DATE NOT NULL,
    status          TEXT NOT NULL CHECK (status IN ('planned', 'running', 'finished'))
);
CREATE INDEX idx_campaigns_department ON campaigns(department_id);

-- ----------------------------------------------------------------------------
--  campaign_metrics —— 活动每日指标
-- ----------------------------------------------------------------------------
CREATE TABLE campaign_metrics (
    id                  BIGSERIAL PRIMARY KEY,
    campaign_id         BIGINT NOT NULL REFERENCES campaigns(campaign_id) ON DELETE CASCADE,
    date                DATE NOT NULL,
    gmv                 NUMERIC(14, 2),
    dau                 INTEGER,
    watch_time          NUMERIC(10, 2),
    conversion_rate     NUMERIC(6, 4),
    UNIQUE (campaign_id, date) -- 同一个 campaign 同一天不能有两条指标记录
);
CREATE INDEX idx_metrics_campaign ON campaign_metrics(campaign_id);

-- ----------------------------------------------------------------------------
--  sop_documents —— SOP 文档元数据
--  department 是 TEXT 不是外键:要支持 'ALL' 这个哨兵值表示"全部门通用",
--  这跟 departments 表本身不是同一个概念,所以不建外键约束(代价:部门改名要
--  手动同步这里,量小可接受,见 docs/permission-model.md)。
-- ----------------------------------------------------------------------------
CREATE TABLE sop_documents (
    doc_id              BIGSERIAL PRIMARY KEY,
    title               TEXT NOT NULL,
    department          TEXT NOT NULL, -- 部门名,或 'ALL' 表示通用
    category            TEXT,
    steps               JSONB NOT NULL DEFAULT '[]'::jsonb,
    responsible_team    TEXT,
    source_path         TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ----------------------------------------------------------------------------
--  doc_chunks —— 文档切片 + 向量(Day 9-11 才会真正写入,这里先建表)
-- ----------------------------------------------------------------------------
CREATE TABLE doc_chunks (
    chunk_id        BIGSERIAL PRIMARY KEY,
    doc_id          BIGINT REFERENCES sop_documents(doc_id) ON DELETE CASCADE,
    chunk_index     INTEGER NOT NULL,
    content         TEXT NOT NULL,
    embedding       VECTOR(1536), -- 维度对齐 text-embedding-3-small
    metadata        JSONB NOT NULL DEFAULT '{}'::jsonb
);
-- HNSW:近似最近邻索引,给"相似度检索"用。等真的有数据了再建也行,
-- 现在先建空索引不影响正确性,后面写入不用改表结构。
CREATE INDEX idx_doc_chunks_embedding ON doc_chunks
    USING hnsw (embedding vector_cosine_ops);

-- ----------------------------------------------------------------------------
--  doc_ingest_status —— 文档处理状态(uploading -> processing -> done/failed)
-- ----------------------------------------------------------------------------
CREATE TABLE doc_ingest_status (
    doc_id          BIGINT PRIMARY KEY REFERENCES sop_documents(doc_id) ON DELETE CASCADE,
    status          TEXT NOT NULL CHECK (status IN ('uploading', 'processing', 'done', 'failed')),
    error           TEXT,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

SELECT 'schema ready' AS status;
