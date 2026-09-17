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
--
--  没有"主播组"这一层:讨论过要不要加(1000人/10部门/5组/组20人的规模假设
--  隐含了组这一级组织架构),决定先不加——组本身、组内权限都先不做,
--  部门这一级权限先跑通再说。
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
--
--  anchor_id:一个 campaign 归属一个主播(简化——真实场景可能多主播联播,
--  先按"一个主责主播"处理)。这是这次新加的字段:策划案/复盘文档要能
--  追溯到"这是哪个主播的活动",campaigns 是这条追溯链路的中间环节
--  (documents.campaign_id -> campaigns.anchor_id -> anchors),
--  不在 documents 上重复冗余存一份 anchor_id。
-- ----------------------------------------------------------------------------
CREATE TABLE campaigns (
    campaign_id     BIGSERIAL PRIMARY KEY,
    name            TEXT NOT NULL,
    department_id   BIGINT NOT NULL REFERENCES departments(department_id),
    anchor_id       BIGINT NOT NULL REFERENCES anchors(anchor_id),
    category        TEXT,
    start_date      DATE NOT NULL,
    end_date        DATE NOT NULL,
    status          TEXT NOT NULL CHECK (status IN ('planned', 'running', 'finished'))
);
CREATE INDEX idx_campaigns_department ON campaigns(department_id);
CREATE INDEX idx_campaigns_anchor ON campaigns(anchor_id);

-- ----------------------------------------------------------------------------
--  campaign_metrics —— 活动每日指标
--  只放"干净的、我们自己就能拿到的数字"。ROI、商品级拆解、成因分析这些
--  需要判断/归因的内容,不放在这里当列——那些是"内容",走 documents
--  (campaign_review 文档)那条 RAG 链路,不是结构化查询该管的事。
-- ----------------------------------------------------------------------------
CREATE TABLE campaign_metrics (
    id                          BIGSERIAL PRIMARY KEY,
    campaign_id                 BIGINT NOT NULL REFERENCES campaigns(campaign_id) ON DELETE CASCADE,
    date                        DATE NOT NULL,
    gmv                         NUMERIC(14, 2),
    dau                         INTEGER,
    watch_time                  NUMERIC(10, 2),
    conversion_rate             NUMERIC(6, 4),
    peak_concurrent_viewers     INTEGER,        -- 当天场次在线人数峰值
    avg_dwell_time_minutes      NUMERIC(6, 2),  -- 当天场次人均停留时长(分钟)
    UNIQUE (campaign_id, date) -- 同一个 campaign 同一天不能有两条指标记录
);
CREATE INDEX idx_metrics_campaign ON campaign_metrics(campaign_id);

-- ----------------------------------------------------------------------------
--  documents —— 文档表(原名 sop_documents,泛化成三种文档类型共用一张表):
--    sop             运营 SOP / 指南
--    campaign_plan   直播策划案(campaign 开播前,用于事后被 AI 分析
--                    "哪些策划特点导致了什么结果"、给未来 campaign 做预测参考)
--    campaign_review 复盘文档(campaign 结束后,里面包含 ROI 等结果数据——
--                    ROI 是文档内容,不是我们单独计算出来存进结构化字段的)
--
--  三种类型走同一条摄入管道(上传 -> Kafka -> 解析 -> 切片 -> embedding),
--  所以共用一张表 + doc_type 字段区分,而不是分三张表、写三遍摄入逻辑。
--
--  department 是 TEXT 不是外键:要支持 'ALL' 这个哨兵值表示"全部门通用"
--  (只有 sop 类型会用到 'ALL';campaign_plan/campaign_review 必然属于
--  某个具体部门,取值应跟对应 campaign 的部门一致)。
-- ----------------------------------------------------------------------------
CREATE TABLE documents (
    doc_id              BIGSERIAL PRIMARY KEY,
    doc_type            TEXT NOT NULL CHECK (doc_type IN ('sop', 'campaign_plan', 'campaign_review')),
    title               TEXT NOT NULL,
    department          TEXT NOT NULL, -- 部门名,或 'ALL'(仅 sop 类型可用)
    campaign_id         BIGINT REFERENCES campaigns(campaign_id), -- 仅 campaign_plan/campaign_review 填
    category            TEXT,
    steps               JSONB NOT NULL DEFAULT '[]'::jsonb, -- 主要给 sop 用,存结构化步骤
    responsible_team    TEXT,
    source_path         TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- sop 类型不该挂 campaign;campaign_plan/campaign_review 必须挂 campaign——
    -- 用 CHECK 把这条业务规则钉在数据库层面,而不是全靠应用代码自觉
    CONSTRAINT chk_document_campaign_link CHECK (
        (doc_type = 'sop' AND campaign_id IS NULL) OR
        (doc_type != 'sop' AND campaign_id IS NOT NULL)
    )
);
CREATE INDEX idx_documents_campaign ON documents(campaign_id);

-- ----------------------------------------------------------------------------
--  doc_chunks —— 文档切片 + 向量(Day 9-11 才会真正写入,这里先建表)
--  metadata 除了 department/category/source_doc/title,以后摄入 campaign_plan/
--  campaign_review 时还会存 doc_type、campaign_id,方便检索时不用 join 就能过滤
--  (呼应 department 的过滤方式,都是靠 metadata jsonb,不是靠额外的表字段)。
-- ----------------------------------------------------------------------------
CREATE TABLE doc_chunks (
    chunk_id        BIGSERIAL PRIMARY KEY,
    doc_id          BIGINT REFERENCES documents(doc_id) ON DELETE CASCADE,
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
    doc_id          BIGINT PRIMARY KEY REFERENCES documents(doc_id) ON DELETE CASCADE,
    status          TEXT NOT NULL CHECK (status IN ('uploading', 'processing', 'done', 'failed')),
    error           TEXT,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

SELECT 'schema ready' AS status;
