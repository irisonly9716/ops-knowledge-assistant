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
├── contracts/              # 服务间消息契约的样例(Java 和 Python 的测试都对着它验证)
├── uploads/                # 上传的文件(运行时生成,gitignore)
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

### 从零跑通(顺序不能乱,Day 7 实测过一遍)

```bash
# 1. 起中间件(Docker Desktop 引擎要先在运行,否则 docker 命令连不上)
cp .env.example .env
docker compose up -d
docker compose ps                     # postgres / redis / kafka 都是 healthy 再往下

# 2. 建 Kafka topic ——必须在上传之前。compose 里关了自动建 topic,
#    topic 不存在时上传接口会在 ~5 秒后返回 503
cd llm-service
uv run python -m scripts.init_topics

# 3. 灌模拟数据 ——必须在上传之前。上传接口会校验 department / campaignId
#    在库里真实存在,库是空的话任何上传都会 400
uv run python -m scripts.generate_mock_data

# 4. 起后端(另开一个终端)
cd ../backend-service
.\mvnw.cmd spring-boot:run            # WSL/macOS/Linux: ./mvnw spring-boot:run

# 5. 起消费者(再另开一个终端)——独立的 worker 进程,不是 FastAPI
cd ../llm-service
uv run python -m app.consumers.upload_consumer

# 6. 验证 上传 → Kafka → 消费者读到文件(消费者的日志里会出现"收到上传 ...")
curl -X POST http://localhost:8081/documents/upload \
  -F "file=@sop_docs/风控问题处理流程.md" -F "contentKind=document" -F "docType=sop" -F "department=ALL"
```

跑测试:`cd backend-service; .\mvnw.cmd test`(39 个,不需要 Docker)和 `cd llm-service; uv run pytest`(不需要 Kafka)。

`docker compose down -v` 会清空所有数据卷(Postgres 数据、Kafka topic 全没),
之后必须重新走 2、3 两步。

### 常见问题

**后端报 `password authentication failed for user "ops"`,但密码明明没错**
十有八九是**本机另有一个 Postgres 也占着 5432 端口**(比如 Windows 上装过原生 PostgreSQL,
服务名 `postgresql-x64-NN`)。Docker 和它同时监听 5432,`localhost:5432` 会连到本机那个——
它没有 `ops` 用户。确认方法(PowerShell):`Get-NetTCPConnection -LocalPort 5432 -State Listen`
会看到两个进程。**不用卸载它**,把项目的端口换掉即可:`.env` 里改 `POSTGRES_PORT=5433`,
再 `docker compose up -d`(只重建 postgres 容器,数据卷保留)。compose、Spring、Python 都读这个变量,
不用改别处。(`.env` 不进 git,所以这个改动只影响你这台机器;`.env.example` 保持默认 5432。)

**`mvnw spring-boot:run` 报 `ClassFormatError` / `Unresolved compilation problem`**
`target/classes` 里有被别的编译器(VS Code 的 Java 扩展用的 Eclipse 编译器)写坏的 class,
Maven 看它比源码新就不重编。`.\mvnw.cmd clean` 一次即可;想省心就每次 `clean spring-boot:run`。

**`uv run python -m scripts.xxx` 报 `No module named 'scripts'`**
要在 `llm-service/` 目录里跑(`cd llm-service`),不是仓库根。

### 验证

```bash
# Postgres(需要 psql 或用 DBeaver 连 localhost:<.env 里的 POSTGRES_PORT,默认 5432>)
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
