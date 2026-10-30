"""HTTP entry point of llm-service (FastAPI).

Only /health for now. POST /query comes later: RAG Q&A, Text-to-SQL, query routing,
conversation memory and caching.

Note: the Kafka consumer is NOT started here. It is a separate worker process
(python -m app.consumers.upload_consumer); see app/consumers/upload_consumer.py for why.
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
