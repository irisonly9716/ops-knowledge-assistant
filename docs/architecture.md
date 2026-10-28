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

## 数据摄取链路(ingestion)—— 两条管道,同一个 topic

上传的东西分两种性质,处理方式完全不同,但共用同一个 Kafka topic(消息体带
`content_kind` 字段区分,consumer 收到后分流,不需要为此另开一个 topic):

**关于 `upload_id` vs `doc_id`**:消息体里带的是 backend-service 自己生成的 `upload_id`
(UUID),不是 `doc_id`——`documents.doc_id` 是 Postgres 的 `BIGSERIAL`,要等 llm-service
真正执行 `INSERT INTO documents ... RETURNING doc_id` 才会产生,backend-service 发消息
那一刻这行还不存在,不可能提前知道。`upload_id` 的另一个用处是 Kafka 消费者的**去重键**
(Kafka "至少一次投递,消费者可能收到重复消息"的问题)。

**关于 `storageKey`**:消息里传的是存储里的【逻辑键】(现在就是文件名),
不是文件路径。最初的版本发的是 `path: "uploads\\xxx.md"`——相对于 backend-service 目录、
带 Windows 反斜杠,消费者在另一个目录甚至另一台机器上根本解析不了。现在两个服务各自读
`.env` 里的 `UPLOAD_DIR` 知道"存储根目录"在哪,自己拼出真实路径;以后换 S3,键不变,只换
存储后端。消费者拼路径时必须防路径穿越(见 `llm-service/app/consumers/storage.py`),
因为消息内容不能盲信。

**消息契约的唯一事实来源**是 [contracts/upload-event.examples.json](../contracts/upload-event.examples.json)。
Java 的 `UploadEventContractTest` 和 Python 的 `tests/test_events.py` 都对着这同一份文件验证,
任何一边悄悄改格式,对应那边的测试就红。

**消费者是独立的 worker 进程**,不是 FastAPI 里的后台线程:
`uv run python -m app.consumers.upload_consumer`。理由:长期阻塞的 poll 循环和 HTTP 的
async 事件循环是两种性质的活;生命周期独立(web 重启不打断正在处理的文档);可以独立伸缩
(同一消费组多开 worker 分摊分区);因为是普通阻塞循环,用同步的 confluent-kafka 即可,不需要 aiokafka。

**投递语义:至少一次。** 关掉自动提交,处理完(或确定进了死信)才手动提交 offset。
失败处理分三级:
- 消息本身坏了(不是合法 JSON / 不符合契约 / 路径越界)→ `PermanentError`,**不重试**,直接进死信
- 处理时的其它异常(文件暂时读不到、下游超时)→ 指数退避重试(默认共尝试 3 次,即第一次加 2 次重试,中间依次等 0.5s、1s),用尽进死信。注意死信 topic 本身也受 Kafka 保留期限制(本项目默认 7 天,`retention.ms=604800000`),过期的死信会被自动删除——死信是"暂存待处理",不是永久存档
- **死信 topic `document-upload-events.dlq`** 原样保存坏消息,header 里带失败原因/尝试次数/来源 offset。
  死信必须**确认写入成功**才提交 offset;写不进去就让进程崩溃——宁可停下来也不丢消息。
  这样坏消息既不会无限重试卡住后面的消息(队头阻塞),也不会被悄悄丢掉。

**① 文档类**(SOP / 策划案 / 复盘的叙述内容)—— 非结构化文字,走 RAG:

```
[员工手动上传]  POST /documents/upload (backend)
      │  存文件到 uploads/ 或 S3,生成 upload_id
      ▼
  Kafka: document-upload-events   { upload_id, storage_key, content_kind: "document",
                                     doc_type, department, campaign_id?, category?, source: "manual" }
      │
[未来] 定时任务  ──发同一个 topic──▶   { ..., source: "auto" }
      │
      ▼
  llm-service Kafka consumer
      │  1. 下载/读取文件
      │  2. 解析(Markdown / docx / 数字版 PDF → 标题、段落、列表项、表格)
      │  3. chunking(RecursiveCharacterTextSplitter)
      │  4. embedding(每个 chunk 一个向量)
      │  5. 写入 Postgres: INSERT documents(此时才产生真正的 doc_id)+ doc_chunks
      │  6. 更新状态 done
      ▼
  能被向量检索
```

**② 结构化表格类**(运营上传的商品售卖表、顾客行为表,比如 Excel/CSV 导出)——
不切片、不 embedding,解析后直接落成结构化行,走 Text-to-SQL:

```
[员工上传 Excel/CSV]  POST /documents/upload (backend,同一个接口)
      │  生成 upload_id
      ▼
  Kafka: document-upload-events   { upload_id, storage_key, content_kind: "data_table",
                                     table_type: "campaign_products" | "campaign_customer_behavior",
                                     campaign_id, source: "manual" }
      ▼
  llm-service Kafka consumer
      │  1. 下载/读取文件
      │  2. 解析表格(pandas / openpyxl)
      │  3. 按 table_type 直接 INSERT 进 campaign_products / campaign_customer_behavior
      │  4. 更新状态 done
      ▼
  能被 Text-to-SQL 查询
```

这两条管道不是我们计算分析引擎——**它们只是把"运营已经有的数据"或"运营写的内容"
存进来,真正的分析(为什么这场结果好/差)交给 LLM,结合 ① 里的策划案/复盘
内容 + ② 里的结构化数据 + `campaign_metrics` 一起做归因,这才是本项目的核心价值。

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
- **为什么文档解析不用 Unstructured,而是每种格式一个小库**(markdown-it-py / python-docx / pdfplumber,放在同一个 `DocumentParser` 接口后面):在同一批样例上实测过。`unstructured` 不带任何格式支持就装了 372 MB(spaCy、numba),加上 PDF 支持是 1.5 GB(torch、OpenCV),而快速模式的 PDF 解析根本用不到这些;解析时还会从 GitHub 下载一个英文 spaCy 模型,拿来判断"这行是标题还是正文",对中文文档意义不大。效果上,它在 PDF 里分不出标题级别,把标题判成正文、正文判成标题,表格拆成几十个零散片段;Markdown 的嵌套列表也被压平。三个小库一共 54 MB,PDF 的结构推断规则是自己写的,三种格式解析出同样的结构(测试覆盖)。以后要支持扫描件,在同一个接口后面加一个 OCR 实现(比如 Amazon Textract)。
- **为什么向量和结构化数据都放 Postgres**:少一个中间件要运维,`doc_chunks` 和业务表能在同一事务/同一连接里查。
- **为什么权限在 schema 层就埋 `department`**:如果等到最后才加,向量表、SQL 生成、检索 filter 全要返工。见 [permission-model.md](permission-model.md)。
- **为什么"文档"和"结构化表格"两种上传共用一个 topic,不是分开两个**:两者的消息形状(`upload_id`/`storage_key`/`department`)本来就大部分重合,分流靠 payload 里一个 `content_kind` 字段 + consumer 内部 if/else 就够了;拆成两个 topic 除了多一次创建/多一份配置,没有实际好处。原则是:topic 该按*语义上不同的事件种类*拆,不该为了图代码分支清晰而拆。
- **为什么 ROI / 商品级归因 / 顾客人群这些不做成结构化字段**:这个项目的核心价值是"AI 分析内容和结果",不是重新造一套 BI 计算引擎。干净的数字(GMV/DAU/转化率/在线人数)进 `campaign_metrics`,需要判断和归因的内容进 `documents`(复盘文档),运营已经有但我们不该重新计算的原始数据(商品售卖、顾客行为)进对应的结构化表,原样接收,不在中间加一层自己的计算逻辑。
