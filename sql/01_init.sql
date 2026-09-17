-- ============================================================================
--  01_init.sql
--  Postgres 容器"第一次创建数据库时"自动执行一次(挂在 /docker-entrypoint-initdb.d)
--  如果改了这个文件要重新生效:  docker compose down -v  再  up
-- ============================================================================

-- 开启 pgvector 扩展 —— 之后才能用 vector 类型和相似度检索
CREATE EXTENSION IF NOT EXISTS vector;

-- 业务表在 02_schema.sql 里(按文件名顺序排在这个文件后面执行),
-- 详细字段说明见 docs/data-schema.md。

SELECT 'pgvector ready' AS status;
