# 架构与运行时链路

## 两个服务的分工

| 服务 | 语言 | 职责 |
|---|---|---|
| **backend-service** | Java / Spring Boot | 对外 REST API、文件上传接口、结构化业务表 CRUD(JPA)、把上传事件发到 Kafka、把 `/query` 转发给 llm-service |
| **llm-service** | Python / FastAPI | Kafka 消费者(文档解析→切片→embedding→入向量库)、RAG 检索、Text-to-SQL、查询路由、会话记忆、缓存 |

两个服务都会用到 **Kafka** 和 **Redis**:

- **Kafka**
  - `document-upload-events`:员工在前端上传文件/SOP → backend 存文件 + 发事件 → llm-service 消费并处理
  - 未来「自动化上传最新数据」:一个定时任务(scheduled producer)按天把最新 campaign / 主播数据打包成文件,发同一个 topic,复用同一条处理链路 —— 所以 topic 的消息体要带 `source: manual | auto` 字段
- **Redis**
  - 查询缓存:相同/相似问题直接返回,不重复走检索 + LLM
  - 会话记忆:`session:{id}` 存多轮对话历史
  - 文档处理状态:`doc:{id}:status` = uploading / processing / done / failed(也可落库)

## 数据摄取链路(ingestion)

```
[员工手动上传]  POST /documents/upload (backend)
      │  存文件到 uploads/ 或 S3
      ▼
  Kafka: document-upload-events   { doc_id, path, department, category, source: "manual" }
      │
[未来] 定时任务  ──发同一个 topic──▶   { ..., source: "auto" }
      │
      ▼
  llm-service Kafka consumer
      │  1. 下载/读取文件
      │  2. 解析(PDF / Markdown / Word)
      │  3. chunking(RecursiveCharacterTextSplitter)
      │  4. embedding(每个 chunk 一个向量)
      │  5. 写入 Postgres: doc_chunks(chunk, embedding, metadata)
      │  6. 更新状态 done
      ▼
  可被检索
```

## 提问链路(query)

```
用户提问 + X-User-Id
      │
      ▼
backend /query ──转发──▶ llm-service /query
      │
      │ 1. 解析当前用户 → department, role
      │ 2. 查缓存(命中直接返回)
      │ 3. 路由:文档类 / 数据类 / 两者都要?
      │
      ├── 文档类 → 向量检索(filter: department IN (用户部门, 'ALL'))
      │
      ├── 数据类 → Text-to-SQL
      │             - schema 拼进 prompt
      │             - 生成 SQL(只允许 SELECT)
      │             - 注入 WHERE department = 用户部门(role=staff 时)
      │             - 执行 → 结果
      │
      │ 4. 检索结果 + SQL 结果 + 问题 + 会话历史 → LLM → 回答
      │ 5. 写缓存 + 写会话记忆
      ▼
   返回给用户
```

## 端口一览(本地开发)

| 服务 | 端口 |
|---|---|
| Postgres | 5432 |
| Redis | 6379 |
| Kafka | 9092 |
| Kafka UI | 8080 |
| backend-service | 8081 |
| llm-service | 8000 |

## 关键设计取舍

- **为什么 llm-service 用 Kafka 消费而不是 backend 直接 HTTP 调**:解析 + embedding 可能几秒到几十秒,上传接口不能被拖住;失败要能重试;未来自动化 producer 也能复用。
- **为什么向量和结构化数据都放 Postgres**:少一个中间件要运维,`doc_chunks` 和业务表能在同一事务/同一连接里查。
- **为什么权限在 schema 层就埋 `department`**:如果等到最后才加,向量表、SQL 生成、检索 filter 全要返工。见 [permission-model.md](permission-model.md)。
