"""llm-service 的 HTTP 入口(FastAPI)。

目前只有 /health。后续会挂 POST /query:RAG 问答、Text-to-SQL、查询路由、会话记忆与缓存。

注意:Kafka 消费者【不在】这里启动,它是独立的 worker 进程
(python -m app.consumers.upload_consumer),理由见 app/consumers/upload_consumer.py。
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
