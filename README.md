# Operations Knowledge Assistant

运营知识助手 —— 一个入口,同时回答运营的两类问题:

| 问题类型 | 例子 | 链路 |
|---|---|---|
| **查 SOP / 指南**(非结构化) | "遇到风控问题第一步做什么、最后一步是什么、找哪个部门协同?" | RAG:文档切片 → embedding → 向量检索 → LLM 总结 |
| **查历史数据**(结构化) | "上个类似 campaign 的 GMV / 转化率是多少?" | Text-to-SQL:自然语言 → SQL → 查库 → LLM 翻译成人话 |
| **两者都要** | "新 campaign 怎么执行?参考下过往类似 campaign 的数据。" | 路由判断 → 两条链路都跑 → 合并回答 |

再加 **多轮对话记忆**(追问带上下文)和 **部门权限**(每个人只看得到自己部门的数据和文档)。

---

## 解决的痛点

运营现在查历史数据靠手翻表格、遇到问题(比如风控)没有现成的处理思路。这个助手把
"过往 campaign 数据 + 主播数据 + SOP 文档" 变成一个能对话的知识入口。

---

## 架构总览

```
                          ┌─────────────────────────────┐
   用户提问 ──────────────▶│      backend-service        │  Spring Boot (Java)
   (带 X-User-Id 请求头)   │  - REST API (/query 等)      │
                          │  - 文件上传 → Kafka producer │
                          │  - 结构化数据 CRUD (JPA)     │
                          └──────────────┬──────────────┘
                                         │ Kafka: document-upload-events
                                         │ HTTP: /query 转发
                                         ▼
                          ┌─────────────────────────────┐
                          │        llm-service          │  Python (FastAPI)
                          │  - Kafka consumer (文档处理) │
                          │  - RAG:chunk/embed/检索     │
                          │  - Text-to-SQL agent        │
                          │  - 查询路由 + 会话记忆       │
                          └──────┬───────────┬──────────┘
                                 │           │
              ┌──────────────────┘           └──────────────────┐
              ▼                                                  ▼
   ┌─────────────────────┐                          ┌─────────────────────┐
   │ PostgreSQL+pgvector │                          │       Redis         │
   │ - 结构化业务表      │                          │ - 查询缓存          │
   │ - 向量表 (chunks)   │                          │ - 会话记忆          │
   └─────────────────────┘                          └─────────────────────┘

   Kafka (KRaft 模式) —— 上传文件与文档处理之间的异步解耦
```

详见 [docs/architecture.md](docs/architecture.md)。

---

## 目录结构

```
ops-knowledge-assistant/
├── docker-compose.yml      # Postgres+pgvector / Redis / Kafka 一键起
├── .env.example            # 环境变量样例(复制成 .env 后填)
├── sql/
│   └── 01_init.sql         # 建库初始化(pgvector 扩展 + schema,schema 在 Day 5 填)
├── backend-service/        # Spring Boot,Java —— Day 4 用 Spring Initializr 生成
├── llm-service/            # Python FastAPI —— embedding / RAG / SQL agent / 路由
├── sop_docs/               # 模拟 SOP 文档(markdown),Day 9 起被摄入 RAG 链路
├── campaign_docs/          # 模拟策划案/复盘文档(markdown),同样走 RAG 链路
└── docs/
    ├── architecture.md     # 架构与运行时链路
    ├── data-schema.md      # 数据表设计(含 users / 权限字段)
    ├── permission-model.md # 部门权限模型
    └── 21-day-plan.md      # 21 天执行计划(任务大纲)+ 进度勾选
```

---

## 快速开始

### 前置(需要你手动装,详见 [docs/21-day-plan.md](docs/21-day-plan.md) Day 1)

1. **WSL2**:管理员打开 PowerShell → `wsl --install` → 重启
2. **Docker Desktop**:装完在 Settings 里确认 "Use the WSL 2 based engine" 打勾
3. JDK 已有(Java 25);Maven 用项目自带的 wrapper,不用装
4. Python 用 `uv`(已装),版本锁 3.12,见 [llm-service/README.md](llm-service/README.md)

### 起中间件

```bash
cp .env.example .env
docker compose up -d
docker compose ps        # 三个服务都是 healthy 才算成功
```

### 验证

```bash
# Postgres(需要 psql 或用 DBeaver 连 localhost:5432)
docker compose exec postgres psql -U ops -d ops_assistant -c "SELECT extname FROM pg_extension;"
# 应看到 vector

# Redis
docker compose exec redis redis-cli ping        # 返回 PONG

# Kafka:列出 topic
docker compose exec kafka /opt/kafka/bin/kafka-topics.sh --bootstrap-server localhost:9092 --list
```

---

## 技术选型说明(面试要能讲清楚"为什么用它")

| 组件 | 作用 | 为什么用它 |
|---|---|---|
| Kafka | 上传文件 → 文档处理 的异步队列 | 上传接口不能被"解析+embedding"这种慢操作阻塞;解耦后处理服务可独立扩容/重试 |
| pgvector | 存文档切片的向量,做相似度检索 | 不用单独引入 Milvus/Pinecone,一个 Postgres 同时装结构化数据和向量,运维简单 |
| Redis | 查询缓存 + 会话记忆 | 相同/相似问题不重复走 LLM,省时间和成本;会话历史需要快读快写 |
| Spring Boot | 对外 API、文件上传、结构化数据 | 团队 Java 技术栈;JPA 操作业务表方便 |
| FastAPI (Python) | LLM / embedding / agent 逻辑 | AI 生态(langchain / llama-index / sentence-transformers)在 Python 最全 |

---

## 进度

见 [docs/21-day-plan.md](docs/21-day-plan.md) —— 每天的任务和勾选状态都在那里维护。
