-- ============================================================================
--  01_init.sql
--  Postgres 容器"第一次创建数据库时"自动执行一次(挂在 /docker-entrypoint-initdb.d)
--  如果改了这个文件要重新生效:  docker compose down -v  再  up
-- ============================================================================

-- 开启 pgvector 扩展 —— 之后才能用 vector 类型和相似度检索
CREATE EXTENSION IF NOT EXISTS vector;

-- ----------------------------------------------------------------------------
--  下面的业务表在 Day 5 设计 schema 时补全,当前只放占位说明。
--  最终会有(详见 docs/data-schema.md):
--    departments      部门
--    users            员工(user_id, name, department_id, role) —— 权限的基准
--    anchors          主播
--    campaigns        活动(带 department 字段)
--    campaign_metrics 活动指标(gmv / dau / watch_time / conversion_rate / date)
--    sop_documents    SOP 文档元数据(title / department / category / steps jsonb / responsible_team)
--    doc_chunks       文档切片 + embedding(vector) + metadata(department / category / source_doc)
--    doc_ingest_status 文档处理状态(uploading -> processing -> done / failed)
-- ----------------------------------------------------------------------------

SELECT 'pgvector ready' AS status;
