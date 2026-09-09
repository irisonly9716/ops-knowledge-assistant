"""llm-service 入口。

Day 1:只有 /health,确认服务能跑起来。
后面会挂:
  - Day 8   Kafka consumer(文档处理)—— 用 lifespan 启动后台任务
  - Day 13  POST /query —— RAG 问答
  - Day 17+ 路由 / 记忆 / 缓存
"""
from fastapi import FastAPI

from app.config import settings

app = FastAPI(title="ops-knowledge-assistant / llm-service", version="0.1.0")


@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "service": "llm-service",
        "kafka": settings.kafka_bootstrap_servers,
        "redis": f"{settings.redis_host}:{settings.redis_port}",
        "postgres": f"{settings.postgres_host}:{settings.postgres_port}",
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host="0.0.0.0", port=settings.llm_service_port, reload=True)
