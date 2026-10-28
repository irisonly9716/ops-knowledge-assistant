-- ============================================================================
--  V3 —— 上传记录、角色调整、切片带部门
--
--    1. users       新增 executive 角色;executive 和 admin 不属于任何运营部门
--    2. uploads     每次上传一行:谁传的、处理到哪一步、是否覆盖、谁审批的
--    3. documents   upload_id + 自然键唯一约束(认定"同一份文档")
--    4. doc_chunks  冗余 department(复合外键保证跟文档一致)+ embedding_model
--    5. 删掉 doc_ingest_status(被 uploads 取代)
--
--  角色和审批规则见 docs/permission-model.md。
--  Flyway 在一个事务里执行整个文件:任何一句失败,这个文件里前面的修改全部回滚,
--  数据库不会停在"改了一半"的状态。
-- ============================================================================

-- ----------------------------------------------------------------------------
--  1. users
-- ----------------------------------------------------------------------------
ALTER TABLE users DROP CONSTRAINT users_role_check;
ALTER TABLE users ADD CONSTRAINT users_role_check
    CHECK (role IN ('staff', 'manager', 'executive', 'admin'));

-- executive(创始人/CEO)和 admin(系统管理员)不属于任何运营部门;staff 和 manager 必须有部门。
-- 顺序不能换:先放开 NOT NULL、把已有 admin 的部门清空,再加约束。
-- 反过来的话,已有的 admin 行不满足新约束,ADD CONSTRAINT 会直接失败。
ALTER TABLE users ALTER COLUMN department_id DROP NOT NULL;
UPDATE users SET department_id = NULL WHERE role = 'admin';
ALTER TABLE users ADD CONSTRAINT chk_users_department_by_role
    CHECK ((department_id IS NULL) = (role IN ('executive', 'admin')));

-- ----------------------------------------------------------------------------
--  2. uploads —— 每次上传一行
--
--  后端收到上传时先写这一行,再发 Kafka;消费者处理时更新状态。所以上传后
--  马上就能查到状态,"还在排队"和"根本没收到"分得清。
--
--  状态:
--    不需要审批:                     received -> processing -> done / failed
--    需要审批:pending_approval --批准--> received -> processing -> done / failed
--                              --拒绝--> rejected
--
--  replaces_upload_id:这次要覆盖的"当前版本"是哪次上传产生的,为空表示新建。
--  真正执行覆盖时再核对一次"当前版本还是不是它";不是的话,说明审批期间已经被
--  别人覆盖过,这次申请作废(乐观并发控制)。版本用上传记录标记、不用 doc_id,
--  是因为数据表上传不产生 documents 行,这样两条管道用同一套规则。
-- ----------------------------------------------------------------------------
CREATE TABLE uploads (
    upload_id           UUID PRIMARY KEY,
    source              TEXT NOT NULL CHECK (source IN ('manual', 'auto')),
    uploaded_by         BIGINT REFERENCES users(user_id),
    content_kind        TEXT NOT NULL CHECK (content_kind IN ('document', 'data_table')),
    doc_type            TEXT CHECK (doc_type IN ('sop', 'campaign_plan', 'campaign_review')),
    table_type          TEXT CHECK (table_type IN ('campaign_products', 'campaign_customer_behavior')),
    department          TEXT NOT NULL,   -- 部门名,或 'ALL'(仅 sop),跟 documents.department 同一套取值
    campaign_id         BIGINT REFERENCES campaigns(campaign_id),
    title               TEXT,            -- sop 必填(自然键的一部分);活动文档的标题由系统生成
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

    -- 人工上传必须知道是谁传的;自动同步(source = auto)没有上传人
    CONSTRAINT chk_uploads_uploader CHECK ((source = 'manual') = (uploaded_by IS NOT NULL)),
    -- 四眼原则:不能审批自己的上传。
    -- 任何一边为空时,<> 的结果是 NULL;CHECK 只拒绝 FALSE,NULL 视为通过,所以"还没审批"不受影响。
    CONSTRAINT chk_uploads_not_self_review CHECK (reviewed_by <> uploaded_by),
    -- 审批人和审批时间要么都有,要么都没有
    CONSTRAINT chk_uploads_review_pair CHECK ((reviewed_by IS NULL) = (reviewed_at IS NULL)),
    -- 跟消息契约同一套组合规则:文档要有 doc_type,只有 sop 不挂活动;数据表要有 table_type 和活动
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
-- 产生当前版本的那次上传。覆盖时 documents 这一行原地更新、doc_id 不变,只把
-- upload_id 换成新的那次上传、chunk 全部重建。doc_id 不变,回答里的引用和 eval 里
-- 标注的 doc_id 就不会因为文档出了新版本而失效。
-- 表现在是空的,所以能直接加 NOT NULL;有数据的表要分三步:加可空列 -> 回填 -> 设 NOT NULL。
ALTER TABLE documents ADD COLUMN upload_id UUID NOT NULL UNIQUE REFERENCES uploads(upload_id);

-- 自然键 1:策划案 / 复盘 —— 一场活动每种只有一份。
-- sop 的 campaign_id 是 NULL;唯一约束里 NULL 和 NULL 不算相等,所以 sop 行之间
-- 永远不会因为这条约束冲突,它实际上只管活动文档。
ALTER TABLE documents ADD CONSTRAINT uq_documents_campaign_doc UNIQUE (campaign_id, doc_type);
-- 上面的唯一约束自带一个以 campaign_id 开头的索引,原来的单列索引多余了
DROP INDEX idx_documents_campaign;

-- 自然键 2:SOP —— 同一部门(或 ALL)下标题不重复。
-- 按规范化后的标题判断重名:NFKC 把全角字母、数字、空格转成半角,btrim 去首尾空格,
-- lower 统一大小写。表里存的还是原标题,只有判断重名时用规范化后的值。
CREATE UNIQUE INDEX uq_documents_sop_title ON documents
    (department, lower(btrim(normalize(title, NFKC))))
    WHERE doc_type = 'sop';

-- 'ALL' 只给 sop 用:活动文档必然属于某个具体部门
ALTER TABLE documents ADD CONSTRAINT chk_documents_all_only_sop
    CHECK (department <> 'ALL' OR doc_type = 'sop');

-- 给 doc_chunks 的复合外键当被引用方。doc_id 本身已经唯一,所以 (doc_id, department)
-- 必然唯一,这条约束不增加任何限制;只是外键要求被引用的列组合上有唯一约束。
ALTER TABLE documents ADD CONSTRAINT uq_documents_id_department UNIQUE (doc_id, department);

-- ----------------------------------------------------------------------------
--  4. doc_chunks
-- ----------------------------------------------------------------------------
-- department:有意的冗余(documents 里已经有了)。检索时直接按这一列过滤、不用 JOIN,
-- 带过滤的向量检索和以后的 RLS 都靠它。原计划放在 metadata JSONB 里,改成正式列,
-- 才能加约束和索引。
-- 冗余的风险是两边对不上,所以用复合外键让数据库保证一致:
--   (doc_id, department) 必须在 documents 里存在 -> chunk 的部门只能等于所属文档的部门
--   ON UPDATE CASCADE:文档改了部门(比如覆盖后改成 ALL),所有 chunk 自动跟着改
--   ON DELETE CASCADE:删文档时 chunk 一起删,跟原来的单列外键一样
-- 复合外键已经包含了原来 doc_id 单列外键的作用,所以删掉旧的。
-- doc_id 改成 NOT NULL:外键列里只要有一列是 NULL,数据库就不检查这条外键,一致性就被绕过了。
ALTER TABLE doc_chunks DROP CONSTRAINT doc_chunks_doc_id_fkey;
ALTER TABLE doc_chunks ALTER COLUMN doc_id SET NOT NULL;
ALTER TABLE doc_chunks ADD COLUMN department TEXT NOT NULL;
ALTER TABLE doc_chunks ADD CONSTRAINT fk_doc_chunks_document
    FOREIGN KEY (doc_id, department) REFERENCES documents (doc_id, department)
    ON UPDATE CASCADE ON DELETE CASCADE;

-- 同一文档里 chunk 序号不重复。它的索引以 doc_id 开头,删文档时级联删 chunk 也用得上
ALTER TABLE doc_chunks ADD CONSTRAINT uq_doc_chunks_doc_index UNIQUE (doc_id, chunk_index);
-- 小部门的检索走精确扫描时,先按部门筛
CREATE INDEX idx_doc_chunks_department ON doc_chunks (department);

-- 生成这条向量的 embedding 模型。embedding 列固定 1536 维,所以这一列只能区分
-- 同样维度的不同模型;换成不同维度的模型,要另加一列或另建一张表。
ALTER TABLE doc_chunks ADD COLUMN embedding_model TEXT NOT NULL;

-- ----------------------------------------------------------------------------
--  5. doc_ingest_status 被 uploads 取代
--  它以 doc_id 为主键:文档写库之前还没有 doc_id,数据表上传根本不产生文档,两种情况都记不了。
-- ----------------------------------------------------------------------------
DROP TABLE doc_ingest_status;
