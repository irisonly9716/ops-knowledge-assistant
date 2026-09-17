# 21 天执行计划 + 进度

来源:任务大纲.pdf。【学】= 先理解概念(能讲清"为什么用它"即可),【做】= 动手实现。
每天预留 4-6 小时。勾选框自己维护。

---

## 第 1 周:环境搭建 + 数据层基础

### Day 1 — 开发环境搭建  ✅ 完成
- [x] 装 **WSL2**:管理员 PowerShell → `wsl --install` → 重启;实际要再跑一次 `wsl --install -d Ubuntu` 才装上发行版
- [x] 装 **Docker Desktop**(装在 `%LOCALAPPDATA%\Programs\DockerDesktop`),`docker run hello-world` 通过
- [x] JDK:已有 Java 25(比大纲的 21 新,兼容,跳过装 21)
- [x] Python 环境:用 `uv`(机器上的 `python` 是坏的商店占位符),版本锁 3.12 —— `uv python install 3.12` → `cd llm-service && uv venv && uv pip install -r requirements.txt`;`uv run uvicorn app.main:app --port 8000` → /health 返回 ok
- [x] 建项目仓库结构 + 推到 GitHub
- [x] 【学】Docker 核心概念:image=只读模板 / container=运行实例(用完即弃)/ volume=容器外持久化存储 / docker-compose=一个 yml 声明多容器一键起。用容器而非本机装:隔离、版本可控、一键复现

### Day 2 — Docker Compose 跑通基础中间件  ✅ 完成
- [x] 【学】docker-compose.yml 结构:services / ports / volumes / environment(+ image tag / healthcheck / 具名卷 vs 绑定挂载)
- [x] 写 docker-compose.yml(Postgres+pgvector / Redis / Kafka / kafka-ui)
- [x] `cp .env.example .env`(密码不进 git,占位符从 .env 注入)
- [x] `docker compose up -d`,`docker compose ps` 三个服务 healthy
- [x] Postgres 装 pgvector:`vector 0.8.6`,`'[1,2,3]'::vector <-> '[4,5,6]'::vector = 5.196` 验证可用
- [x] Redis:`ping` → PONG,set/get 正常
- 备注:踩坑 —— `bitnami/kafka:3.7` 被 Bitnami 从 Docker Hub 撤了,改用 `apache/kafka:3.9.0`

### Day 3 — Kafka 环境 + 基础收发测试  ✅ 完成
- [x] 【学】Kafka 核心概念:topic / producer / consumer / partition,为什么要它(异步解耦)
- [x] docker-compose 里加 Kafka(KRaft 模式,无需 Zookeeper)
- [x] 创建测试 topic `document-upload-events`(1 partition / 1 replica),console 收发一条 JSON 验证 round-trip
- [x] Python producer + consumer 脚本(`llm-service/scripts/kafka_{producer,consumer}_test.py`,confluent-kafka),验证发→收
- 备注:踩坑 —— PowerShell 管道发的消息带 UTF-8 BOM,`json.loads` 解析报错;consumer 改用 `utf-8-sig` 解码更抗糙。顺带验证了 `finally: consumer.close()` 会触发 offset 提交(即使中途异常)

### Day 4 — Spring Boot 骨架  ✅ 完成
- [x] 用 Spring Initializr 建项目(curl 调 start.spring.io:Web / JPA / PostgreSQL / Kafka / Actuator / Lombok),实际拿到 Spring Boot 4.1.1
- [x] 写 `GET /health`,跑起来确认能访问;`/actuator/health` 也留着给基础设施用
- [x] 配 application.yml 连 Postgres(默认值对齐 .env.example),`Department` Entity + Repository,`DepartmentStartupCheck`(CommandLineRunner)验证写入+读回
- [ ] 【学】Spring Kafka:`KafkaTemplate` 发、`@KafkaListener` 收 —— 留到 Day 6 写生产者时再学,到时候直接对着代码讲
- 备注:8080 被 kafka-ui 占了,backend 端口定为 8081;`ddl-auto: update` 只是 Day 4 临时验证用,Day 5 定稿 SQL DDL 后要改成 validate/none

### Day 5 — 数据 Schema 设计  ✅ 完成
- [x] 设计表:departments / users / anchors / campaigns / campaign_metrics / sop_documents / doc_chunks / doc_ingest_status —— 真正的 DDL 落地在 [sql/02_schema.sql](../sql/02_schema.sql)(BIGSERIAL 主键对齐 Hibernate,doc_chunks 建了 HNSW 索引)
- [x] 写模拟数据生成脚本(Python + Faker + psycopg,`llm-service/scripts/generate_mock_data.py`),插入数据库:4 部门 / 24 用户(含 1 admin)/ 15 主播 / 49 campaign / 1715 条每日指标
- [x] 定 SOP 文档结构:frontmatter(title / department / category / responsible_team)+ markdown 正文,约定见 [sop_docs/README.md](../sop_docs/README.md)
- [x] 写了 5 篇模拟 SOP(2 篇 `ALL` 通用 + 3 篇分部门),见 [sop_docs/](../sop_docs/)
- [x] **`department_id` / `users` / `role` 已经埋进 schema**(见 [permission-model.md](permission-model.md)),`sop_documents.department` 故意用 TEXT 不用外键,为了能存 `ALL` 哨兵值
- 备注:`backend-service` 的 `ddl-auto` 从 `update` 切到 `validate`,`DepartmentStartupCheck.java` 已删除(职责被 Faker 脚本取代)

### Day 6 — 文件上传 API + Kafka 生产者打通
- [ ] Spring Boot `POST /documents/upload`,接收文件存到本地/临时目录(或 S3)
- [ ] 上传成功后往 `document-upload-events` 发消息(含文件路径、department、category、`source: manual`)
- [ ] 用 Postman 上传测试文件 → 命令行 consumer 确认收到

### Day 7 — 缓冲 + 第 1 周回顾
- [ ] 补齐没跑通的部分
- [ ] 自查:compose 一键起 PG+Redis+Kafka?上传能触发 Kafka 消息?模拟数据入库了?
- [ ] 【学】pgvector 基本用法(怎么存 embedding、怎么做相似度查询)

---

## 第 2 周:文档处理 + 核心 RAG 链路

### Day 8 — Kafka 消费者服务(Python)
- [ ] 写 Python 消费者服务,监听 `document-upload-events`
- [ ] 最简版:收到消息 → 打印日志 → 读取对应文件,确认链路跑通

### Day 9 — 文档解析 + Chunking
- [ ] 【学】chunking 是什么、为什么需要
- [ ] 用 langchain / llama-index 的文档加载器,支持 PDF / Markdown / Word
- [ ] 实现 chunking(RecursiveCharacterTextSplitter,设定 chunk_size + overlap),对 SOP 测试

### Day 10 — Embedding 生成
- [ ] 【学】embedding 是什么
- [ ] 选模型(OpenAI text-embedding-3-small 或 sentence-transformers),跑通"文本 → 向量"
- [ ] 把 Day 9 的 chunk 批量生成 embedding

### Day 11 — 向量库存储 + 检索测试
- [ ] chunk + embedding + metadata(department / category / source_doc)存入 pgvector
- [ ] 写相似度检索函数:问题 → embedding → 查最相似的几个 chunk
- [ ] 手动测几个问题(如"风控问题怎么处理"),看检索结果是否相关

### Day 12 — 打通完整消费者链路
- [ ] 把 Day 9-11 整合进 Day 8 的消费者:收事件 → 解析 → chunk → embed → 入库
- [ ] 更新处理状态(uploading → processing → done)

### Day 13 — 基础问答 Endpoint(先不做路由)
- [ ] `POST /query`:接收问题 → 检索 → chunk + 问题给 LLM → 返回回答
- [ ] 端到端测试:上传 SOP → 等处理完 → 提问 → 确认合理回答

### Day 14 — 缓冲 + 第 2 周回顾
- [ ] 补齐,重点检查"上传 → 处理 → 检索 → 问答"主链路
- [ ] 【学】Text-to-SQL 基本思路,看几个 langchain SQL agent 示例

---

## 第 3 周:结构化查询 + 路由 + 记忆

### Day 15 — 结构化数据查询基础
- [ ] 把 schema 整理成文字/JSON,作为 prompt 一部分
- [ ] 写最简 Text-to-SQL:自然语言 → LLM 生成 SQL → 执行 → 返回结果

### Day 16 — Text-to-SQL 完善 + 安全性
- [ ] 测更复杂的问题(对比 / 聚合)
- [ ] 安全限制:只允许 SELECT,拒绝 DROP / DELETE
- [ ] SQL 结果再过一次 LLM,翻译成自然语言

### Day 17 — 查询路由(Router)
- [ ] 【学】意图分类思路:用 prompt 让 LLM 判断"查文档 / 查数据 / 都要"
- [ ] 写路由逻辑:问题进来 → 判断类型 → 调 Day 13 文档检索 或 Day 16 SQL,或都调

### Day 18 — 综合回答生成
- [ ] "两者都要"时,文档检索结果 + SQL 结果一起给 LLM 生成综合回答
- [ ] 测跨类型问题(如"新 campaign 怎么执行,参考过往类似 campaign 数据")

### Day 19 — Redis 查询缓存
- [ ] 【学】缓存逻辑
- [ ] `/query` 加 Redis 缓存层:先查缓存(简单版:完整问题字符串做 key),命中直接返回

### Day 20 — Redis 会话记忆 + 多轮对话
- [ ] 设计 session 结构(Redis 存对话历史),实现多轮:追问带上下文
- [ ] "追问澄清"逻辑:LLM 判断问题模糊时先反问用户

### Day 21 — 权限控制启动 + 缓冲
- [ ] 向量检索加 metadata 过滤(department)
- [ ] SQL 查询加行级过滤(`WHERE department_id = 当前用户部门`)
- [ ] 自查:路由能分三类?多轮上下文有保留?缓存生效?权限过滤跑通?

---

## 后续(Day 22-30,第 4 周,暂不细化)
部署(Docker + AWS)、评估集搭建、README + 面试话术整理。

## 如果某天卡住
优先保证「上传 → 检索 → 基础问答」主链路完整,其他部分简化实现,不要让一天的卡壳拖垮后面。
