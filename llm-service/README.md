# llm-service

Python / FastAPI。负责 embedding、RAG、Text-to-SQL、查询路由、会话记忆。

## 本地跑起来(Day 1 验证)

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
├── consumers/     Day 8+   Kafka 消费者(文档处理链路)
├── rag/           Day 9-13 解析 / chunk / embed / 检索 / 问答
├── sql_agent/     Day 15-16 Text-to-SQL + 安全校验 + 行级过滤
├── router/        Day 17-18 查询路由 + 综合回答
└── memory/        Day 19-20 Redis 缓存 + 会话记忆
tests/
└── test_health.py
```

## 测试

```bash
uv pip install pytest
uv run pytest
```
