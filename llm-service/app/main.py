"""HTTP entry point of llm-service (FastAPI). Start it with `python -m app.server` (see app/server.py).

/health and POST /query (app/query_api.py). Text-to-SQL, query routing, conversation memory and caching come later.

Note: the Kafka consumer is NOT started here. It is a separate worker process
(python -m app.consumers.upload_consumer); see app/consumers/upload_consumer.py for why.
"""
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app import db
from app.config import settings
from app.query_api import router as query_router
from app.rag.answering import QueryService
from app.rag.query_models import OpenAIAnswerModel, OpenAIQueryEmbedder, openai_client_from_settings


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Refuses to start when /query could never work, instead of failing later on the first user's question.
    Postgres being down is not one of those cases: the pool keeps trying, and it can come back."""
    db.require_compatible_event_loop()
    if not settings.internal_service_token:
        raise RuntimeError("INTERNAL_SERVICE_TOKEN is not set: add it to .env at the repository root; the backend uses the same value")
    client = openai_client_from_settings()
    pool = db.query_pool()
    await pool.open()
    app.state.query_service = QueryService(
        OpenAIQueryEmbedder(client, settings.embedding_model, settings.query_embedding_timeout_seconds),
        OpenAIAnswerModel(client, settings.chat_model, settings.query_model_timeout_seconds, settings.answer_max_output_tokens),
        pool, min_similarity=settings.query_min_similarity, top_k=settings.query_top_k)
    try:
        yield
    finally:
        await pool.close()
        await client.close()


app = FastAPI(title="ops-knowledge-assistant / llm-service", version="0.1.0", lifespan=lifespan)
app.include_router(query_router)


# Every error has the same shape as the backend's: {"error": "..."}
@app.exception_handler(StarletteHTTPException)
async def http_error(_: Request, e: StarletteHTTPException) -> JSONResponse:
    return JSONResponse({"error": str(e.detail)}, status_code=e.status_code)


@app.exception_handler(RequestValidationError)
async def invalid_request(_: Request, e: RequestValidationError) -> JSONResponse:
    # Only where and why, never the input: it can hold the question
    details = "; ".join(f"{'.'.join(str(part) for part in error['loc'][1:]) or 'body'}: {error['msg']}" for error in e.errors())
    return JSONResponse({"error": details}, status_code=400)


@app.exception_handler(Exception)
async def unexpected_error(_: Request, e: Exception) -> JSONResponse:
    return JSONResponse({"error": "internal error"}, status_code=500)


@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "service": "llm-service",
        "kafka": settings.kafka_bootstrap_servers,
        "redis": f"{settings.redis_host}:{settings.redis_port}",
        "postgres": f"{settings.postgres_host}:{settings.postgres_port}",
    }
