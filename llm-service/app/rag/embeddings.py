"""Embeddings: turning text into vectors.

Callers (ingestion and retrieval, later) depend only on the small Embedder interface, not on
whether OpenAI or another model is behind it. Tests swap in a fake implementation: no network,
no cost. It is the same idea as the consumer taking its Kafka client as a parameter.

Retries and rate limits:
  - embed_in_batches sends many texts per request, so a document costs one request, not one per chunk
  - OpenAIEmbedder retries network errors, timeouts, 408/409/429 and 5xx itself, with exponential
    backoff and honoring the server's Retry-After, but only as long as the TimeBudget allows. The SDK's
    own retries are switched off (max_retries=0): it would wait up to 120 s per Retry-After with no idea
    of how long the whole message may take, and retries in two places multiply
  - what still fails goes back to the consumer, which retries the whole message within the same budget
    and finally dead-letters it
At this volume (a few documents a day) no client-side throttle is needed on top.
"""
import logging
import random
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from openai import APIConnectionError, APIStatusError, OpenAI

from app.config import settings
from app.time_budget import TimeBudget

log = logging.getLogger("embeddings")

# doc_chunks.embedding is VECTOR(1536). A vector of another size can't be written to the
# database, so catch it here, with a clearer message about which step went wrong than the
# database's "expected 1536 dimensions"
EMBEDDING_DIMENSIONS = 1536
# Texts per request. The API takes up to 2,048 inputs and 300,000 tokens per request; 128 chunks of at
# most 600 tokens stay far below both
EMBEDDING_BATCH_SIZE = 128
# When the caller gives no budget (a one-off script, say)
DEFAULT_BUDGET_SECONDS = 60.0
INITIAL_BACKOFF_SECONDS = 0.5
MAX_BACKOFF_SECONDS = 8.0
RETRYABLE_STATUS = {408, 409, 429}   # plus every 5xx


@dataclass(frozen=True)
class EmbeddingBatch:
    vectors: list[list[float]]  # one vector per input text, in input order
    total_tokens: int           # tokens billed for this call; cost is computed from it


class Embedder(Protocol):
    model: str

    def embed(self, texts: list[str], budget: TimeBudget | None = None) -> EmbeddingBatch: ...


class OpenAIEmbedder:
    def __init__(self, client: OpenAI, model: str, dimensions: int = EMBEDDING_DIMENSIONS,
                 request_timeout: float = 20.0, sleep: Callable[[float], None] = time.sleep):
        self._client = client
        self.model = model
        self.dimensions = dimensions
        self._request_timeout = request_timeout
        self._sleep = sleep

    def embed(self, texts: list[str], budget: TimeBudget | None = None) -> EmbeddingBatch:
        if not texts:
            return EmbeddingBatch(vectors=[], total_tokens=0)  # nothing to embed, no point sending a request
        if any(not text.strip() for text in texts):
            # The API rejects empty strings, and an empty-text embedding means nothing anyway:
            # it points to a chunking bug upstream
            raise ValueError("cannot embed empty text")

        response = self._create_with_retries(texts, budget or TimeBudget(DEFAULT_BUDGET_SECONDS))
        # Each result carries the position (index) of its input; sort by it instead of relying
        # on the order the API returns
        vectors = [item.embedding for item in sorted(response.data, key=lambda item: item.index)]
        if len(vectors) != len(texts):
            raise ValueError(f"sent {len(texts)} texts but received {len(vectors)} vectors")
        for vector in vectors:
            if len(vector) != self.dimensions:
                raise ValueError(
                    f"{self.model} returned a {len(vector)}-dimensional vector; the embedding column "
                    f"has {self.dimensions} dimensions")
        return EmbeddingBatch(vectors=vectors, total_tokens=response.usage.total_tokens)

    def _create_with_retries(self, texts: list[str], budget: TimeBudget) -> Any:
        failures = 0
        while True:
            remaining = budget.remaining()
            if remaining <= 0:
                raise TimeoutError("the time budget ran out before the embedding request could be sent")
            try:
                return self._client.embeddings.create(
                    model=self.model,
                    input=texts,
                    # text-embedding-3 models accept an output dimension. Passing it explicitly lets
                    # 3-large also produce 1536 dimensions for the same column (fewer dimensions cost a
                    # little quality; the model allows that trade-off)
                    dimensions=self.dimensions,
                    timeout=min(self._request_timeout, remaining),
                )
            except APIConnectionError as e:   # includes APITimeoutError
                error, retry_after = e, None
            except APIStatusError as e:
                if e.status_code not in RETRYABLE_STATUS and e.status_code < 500:
                    raise   # 400, 401, 403, 404...: the same request will fail the same way
                error, retry_after = e, _retry_after_seconds(e.response.headers)

            failures += 1
            wait = retry_after if retry_after is not None else _backoff_seconds(failures)
            if wait >= budget.remaining():
                raise error   # waiting would use up the budget anyway: give up now, with the real reason
            log.warning("embedding request failed (%s), retry %d in %.1f s", type(error).__name__, failures, wait)
            self._sleep(wait)


def _backoff_seconds(failures: int) -> float:
    """0.5 s, 1 s, 2 s ... up to 8 s, minus up to 25% jitter so that many clients don't retry in step."""
    delay = min(INITIAL_BACKOFF_SECONDS * 2 ** (failures - 1), MAX_BACKOFF_SECONDS)
    return delay * (1 - 0.25 * random.random())


def _retry_after_seconds(headers: Any) -> float | None:
    """The server's own idea of when to retry, if it gave one in seconds."""
    try:
        seconds = float(headers.get("retry-after"))
    except (TypeError, ValueError):
        return None   # absent, or an HTTP date: fall back to our backoff
    return seconds if seconds > 0 else None


def embed_in_batches(embedder: Embedder, texts: list[str], batch_size: int = EMBEDDING_BATCH_SIZE,
                     budget: TimeBudget | None = None) -> EmbeddingBatch:
    """Embed any number of texts with as few requests as possible: one request per batch_size texts.
    One request per chunk would multiply the round trips and use up the requests-per-minute limit
    long before the tokens-per-minute limit. All batches share the one budget."""
    vectors: list[list[float]] = []
    total_tokens = 0
    for start in range(0, len(texts), batch_size):
        batch = embedder.embed(texts[start:start + batch_size], budget)
        vectors += batch.vectors
        total_tokens += batch.total_tokens
    return EmbeddingBatch(vectors=vectors, total_tokens=total_tokens)


def openai_embedder_from_settings() -> OpenAIEmbedder:
    if not settings.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY is not set: add it to .env at the repository root (.env is not in git)")
    client = OpenAI(api_key=settings.openai_api_key, base_url=settings.openai_base_url, max_retries=0)
    return OpenAIEmbedder(client, settings.embedding_model, request_timeout=settings.embedding_request_timeout_seconds)
