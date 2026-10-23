# llm-service

Python / FastAPI。负责 embedding、RAG、Text-to-SQL、查询路由、会话记忆。

## 本地跑起来

Python 版本锁定 **3.12**(见 `.python-version`)。推荐用 [uv](https://docs.astral.sh/uv/):

```bash
cd llm-service
uv python install 3.12
uv venv
uv pip install -r requirements.txt
uv run uvicorn app.main:app --reload --port 8000
```

打开 http://localhost:8000/health,返回 `{"status":"ok",...}` 就算成功。
http://localhost:8000/docs 是自动生成的接口文档。

<details>
<summary>没有 uv,用标准 venv</summary>

```bash
cd llm-service
py -3.12 -m venv .venv          # 需要先装好 Python 3.12
.venv\Scripts\Activate.ps1      # WSL/macOS: source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```
</details>

## 目录

```
app/
├── main.py        FastAPI 入口(现在只有 /health)
├── config.py      读环境变量(根目录 .env)
├── consumers/     Kafka 消费者(文档处理链路),独立的 worker 进程
│   ├── upload_consumer.py   消费循环:手动提交 + 重试 + 死信 + 优雅退出;入口 main()
│   ├── events.py            消息契约的 Python 一半(pydantic,镜像 Java 的 UploadEvent)
│   ├── storage.py           storageKey -> 上传目录里的真实文件(含路径穿越防御)
│   ├── handlers.py          收到合法事件后具体干什么(解析/切片/向量化/入库逐步往里加)
│   └── errors.py            PermanentError:重试无意义的错误,直接进死信
├── rag/           解析 / chunk / embed / 检索 / 问答
├── sql_agent/     Text-to-SQL + 安全校验 + 行级过滤
├── router/        查询路由 + 综合回答
└── memory/        Redis 缓存 + 会话记忆
tests/             pytest;test_events(契约/跨语言漂移) test_storage(路径穿越)
                   test_upload_consumer(消费循环所有分支) test_health
```

## 跑消费者

```bash
uv run python -m app.consumers.upload_consumer
```

**它是独立于 FastAPI 的进程**(`uvicorn app.main:app` 是另一个)。前置:Kafka 起着、
topic 已建(`init_topics`,其中包含死信 topic `document-upload-events.dlq`)。
启动日志会打出实际解析到的 `upload_dir`——上传目录对不上(两个服务指向不同目录)是最常见的部署问题。

行为要点(设计理由见 [docs/architecture.md](../docs/architecture.md)):
- **至少一次**:关掉自动提交,处理完(或确定进了死信)才提交 offset
- 消息本身坏了(非 JSON / 不符合契约 / 路径越界)→ 不重试,直接进死信 `document-upload-events.dlq`
- 其它异常 → 指数退避重试(默认共尝试 3 次 = 第一次 + 2 次重试,中间等 0.5s、1s),用尽进死信;死信里带失败原因、尝试次数、来源 offset
- 死信写不进去 → 不提交 offset 并崩溃(宁可停也不丢消息)
- 被强杀后重启,新消费者要等旧成员的**会话超时**(约 45 秒)才会接手分区;Ctrl+C 优雅退出则立刻让位

不经过 Java 后端,单独验证消费者:`uv run python -m scripts.kafka_producer_test`
(往上传目录写一个文件,并发一条符合契约的事件)。

## 初始化 Kafka topic

```bash
uv run python -m scripts.init_topics
```

幂等:topic 已存在就跳过。项目需要哪些 topic,都集中列在这个脚本的 `TOPICS` 里 ——
新机器 / `docker compose down -v` 重来后,跑一次这个,不用再手敲 `kafka-topics.sh`。

## 测试

```bash
uv run pytest        # pytest / httpx 已在 requirements.txt 里,不用另装
```

全部用假的 Consumer/Producer/sleep,不需要 Kafka 在跑,不真等退避时间。
`test_events.py` 里有个跨语言漂移检测:直接解析 `sql/02_schema.sql`,断言 `DocType` 枚举跟
`documents.doc_type` 的 CHECK 约束一致——有人改了 SQL 忘了改 Python,测试会红。
另有一个 Windows 上会被跳过的测试(符号链接需要管理员权限),属正常。
