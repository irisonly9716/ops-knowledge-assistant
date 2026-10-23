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
    status          TEXT NOT NULL CHECK (status IN ('planned', 'running', 'finished')),
    -- 效率优化,不是权威数据源:真相永远是"documents 表里存不存在这个 campaign_id
    -- 对应 doc_type 的一行"。这两个字段只是让路由逻辑在决定"要不要做一次向量检索"
    -- 之前,能先用一次便宜的查询判断"根本没有策划案/复盘,不用检索了"。
    -- 代价:摄入代码插入/删除 documents 时,必须记得同步更新这两个字段,
    -- 忘了同步就会跟 documents 表的真实状态不一致——这是主动接受的冗余,不是疏忽。
    has_plan_doc    BOOLEAN NOT NULL DEFAULT FALSE,
    has_review_doc  BOOLEAN NOT NULL DEFAULT FALSE,
    -- 运营成本(坑位费+推广+赠品,不含商品成本——商品成本走 campaign_products)。
    -- 分"计划"和"实际"两个:策划阶段填 planned(预算),活动结束、账单/发票都
    -- 到齐之后才能填 actual。actual 允许为空——campaign 刚开始跑的时候还不知道。
    planned_operating_cost   NUMERIC(14, 2),
    actual_operating_cost    NUMERIC(14, 2)
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
--  campaign_products —— 商品维度的售卖数据,运营上传(比如导出的 Excel/CSV),
--  不是我们计算的——这是"结构化表格上传"，跟下面的 documents(文档上传)
--  是两条不同的摄入管道:documents 是非结构化文字，要切片+embedding 走 RAG；
--  这张表和 campaign_customer_behavior 是结构化表格，解析后直接 INSERT 成行，
--  不切片不 embedding，靠 Text-to-SQL 查询。见 docs/architecture.md 的说明。
-- ----------------------------------------------------------------------------
CREATE TABLE campaign_products (
    id                  BIGSERIAL PRIMARY KEY,
    campaign_id         BIGINT NOT NULL REFERENCES campaigns(campaign_id) ON DELETE CASCADE,
    product_name        TEXT NOT NULL,
    units_sold          INTEGER,
    sell_through_rate   NUMERIC(5, 4), -- 售出率
    return_rate         NUMERIC(5, 4), -- 退货率
    sales_amount        NUMERIC(14, 2),
    click_through_rate  NUMERIC(5, 4), -- 点击率
    cost_price          NUMERIC(10, 2), -- 单件成本价
    profit              NUMERIC(14, 2), -- 这个商品在这场活动的利润总额
    profit_margin       NUMERIC(5, 4),  -- 利润率
    UNIQUE (campaign_id, product_name)
);
CREATE INDEX idx_products_campaign ON campaign_products(campaign_id);

-- ----------------------------------------------------------------------------
--  campaign_customer_behavior —— 顾客行为数据,运营上传,同样不是我们计算的。
--  customer_ref 是匿名化标识(不存真实姓名/手机号)。以后要算"复购率"，
--  不需要另建一套订单系统——直接按 customer_ref 分组、数一下这个顾客出现在
--  几个不同 campaign 里，就是一句普通的 SQL,不是什么复杂的分析引擎。
-- ----------------------------------------------------------------------------
CREATE TABLE campaign_customer_behavior (
    id                      BIGSERIAL PRIMARY KEY,
    campaign_id             BIGINT NOT NULL REFERENCES campaigns(campaign_id) ON DELETE CASCADE,
    customer_ref            TEXT NOT NULL,
    region                  TEXT,
    dwell_time_minutes      NUMERIC(6, 2),
    products_purchased      JSONB DEFAULT '[]'::jsonb, -- 顾客可能买了不止一件商品
    purchase_amount         NUMERIC(10, 2),
    ip_address              TEXT
);
CREATE INDEX idx_behavior_campaign ON campaign_customer_behavior(campaign_id);
CREATE INDEX idx_behavior_customer ON campaign_customer_behavior(customer_ref); -- 给以后跨 campaign 找同一顾客用

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
--  doc_chunks —— 文档切片 + 向量(由文档摄入链路写入)
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
