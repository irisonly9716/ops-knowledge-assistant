"""HTTP entry point of llm-service (FastAPI). Start it with `python -m app.server` (see app/server.py).

/health and POST /query (app/query_api.py), which answers through app/query_flow.py: routing, the documents and the
campaign data. Conversation memory and caching come later.

Note: the Kafka consumer is NOT started here. It is a separate worker process
(python -m app.consumers.upload_consumer); see app/consumers/upload_consumer.py for why.
"""
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from openai import AsyncOpenAI
from starlette.exceptions import HTTPException as StarletteHTTPException

from app import db
from app.config import settings
from app.query_api import router as query_router
from app.query_flow import QueryFlow
from app.rag.answering import QueryService
from app.rag.query_models import (ModelCallLimit, OpenAIAnswerModel, OpenAIQueryEmbedder, OpenAIStructuredModel,
                                  openai_client_from_settings)
from app.rag.reranking import Reranker, tei_reranker_from_settings
from app.router import routing
from app.router.routing import QuestionRouter
from app.sql_agent.pipeline import SqlOptions, SqlService


def query_flow_from_settings(client: AsyncOpenAI, pool: Any, reader_pool: Any, reranker: Reranker | None) -> QueryFlow:
    """The query path's parts with their settings. The evaluation (evaluation/flow_run.py) builds it here too, so it
    measures what /query runs."""
    # One limit for every model call of the process: the router, embeddings, answers and queries share the same slots
    limit = ModelCallLimit(settings.model_max_concurrent_calls, settings.model_slot_wait_seconds)
    embedder = OpenAIQueryEmbedder(client, settings.embedding_model, limit, settings.query_embedding_timeout_seconds)
    answers = OpenAIAnswerModel(client, settings.chat_model, limit, settings.query_model_timeout_seconds,
                                settings.answer_max_output_tokens)
    documents = QueryService(embedder, answers, pool, min_similarity=settings.query_min_similarity,
                             top_k=settings.query_top_k, reranker=reranker, rerank_candidates=settings.rerank_candidates)
    data = SqlService(embedder, OpenAIAnswerModel(client, settings.sql_model, limit, settings.query_model_timeout_seconds,
                                                  settings.answer_max_output_tokens),
                      answers, pool, reader_pool, SqlOptions.from_settings())
    router = QuestionRouter(OpenAIStructuredModel(client, settings.router_model, limit, settings.router_timeout_seconds,
                                                  routing.MAX_OUTPUT_TOKENS),
                            min_confidence=settings.router_min_confidence, timeout_seconds=settings.router_timeout_seconds)
    composer = OpenAIAnswerModel(client, settings.chat_model, limit, settings.query_model_timeout_seconds,
                                 settings.answer_max_output_tokens, reasoning_effort=settings.compose_reasoning_effort)
    return QueryFlow(embedder, router, documents, data, composer)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Refuses to start when /query could never work, instead of failing later on the first user's question.
    Postgres being down is not one of those cases: the pool keeps trying, and it can come back."""
    db.require_compatible_event_loop()
    if not settings.internal_service_token:
        raise RuntimeError("INTERNAL_SERVICE_TOKEN is not set: add it to .env at the repository root; the backend uses the same value")
    client = openai_client_from_settings()
    pool, reader_pool = db.query_pool(), db.sql_pool()   # sql_pool refuses to be made without the reader's password
    await pool.open()
    await reader_pool.open()
    reranker = tei_reranker_from_settings()   # not checked here: a reranker that is down only costs answer quality
    app.state.query_flow = query_flow_from_settings(client, pool, reader_pool, reranker)
    try:
        yield
    finally:
        await pool.close()
        await reader_pool.close()
        await client.close()
        if reranker:
            await reranker.aclose()


app = FastAPI(title="ops-knowledge-assistant / llm-service", version="0.1.0", lifespan=lifespan)
app.include_router(query_router)


# Every error has the same shape as the backend's: {"error": "..."}
@app.exception_handler(StarletteHTTPException)
async def http_error(_: Request, e: StarletteHTTPException) -> JSONResponse:
    return JSONResponse({"error": str(e.detail)}, status_code=e.status_code, headers=e.headers)


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
