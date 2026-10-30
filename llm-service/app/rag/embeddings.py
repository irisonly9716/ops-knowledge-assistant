"""Embeddings: turning text into vectors.

Callers (ingestion and retrieval, later) depend only on the small Embedder interface, not on
whether OpenAI or another model is behind it. Tests swap in a fake implementation: no network,
no cost. It is the same idea as the consumer taking its Kafka client as a parameter.

Retries and rate limits are layered:
  - embed_in_batches sends many texts per request, so a document costs one request, not one per chunk
  - the OpenAI SDK retries network errors, 429 (rate limited) and 5xx responses itself, with
    exponential backoff and honoring the server's Retry-After (settings.openai_max_retries)
  - what still fails after that goes back to the consumer, which retries the whole message and finally
    dead-letters it
At this volume (a few documents a day) no client-side throttle is needed on top.
"""
from dataclasses import dataclass
from typing import Protocol

from openai import OpenAI

from app.config import settings

# doc_chunks.embedding is VECTOR(1536). A vector of another size can't be written to the
# database, so catch it here, with a clearer message about which step went wrong than the
# database's "expected 1536 dimensions"
EMBEDDING_DIMENSIONS = 1536
# Texts per request. The API takes up to 2,048 inputs and 300,000 tokens per request; 128 chunks of at
# most 600 tokens stay far below both
EMBEDDING_BATCH_SIZE = 128


@dataclass(frozen=True)
class EmbeddingBatch:
    vectors: list[list[float]]  # one vector per input text, in input order
    total_tokens: int           # tokens billed for this call; cost is computed from it


class Embedder(Protocol):
    model: str

    def embed(self, texts: list[str]) -> EmbeddingBatch: ...


class OpenAIEmbedder:
    def __init__(self, client: OpenAI, model: str, dimensions: int = EMBEDDING_DIMENSIONS):
        self._client = client
        self.model = model
        self.dimensions = dimensions

    def embed(self, texts: list[str]) -> EmbeddingBatch:
        if not texts:
            return EmbeddingBatch(vectors=[], total_tokens=0)  # nothing to embed, no point sending a request
        if any(not text.strip() for text in texts):
            # The API rejects empty strings, and an empty-text embedding means nothing anyway:
            # it points to a chunking bug upstream
            raise ValueError("cannot embed empty text")

        response = self._client.embeddings.create(
            model=self.model,
            input=texts,
            # text-embedding-3 models accept an output dimension. Passing it explicitly lets
            # 3-large also produce 1536 dimensions for the same column (fewer dimensions cost a
            # little quality; the model allows that trade-off)
            dimensions=self.dimensions,
        )
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


def embed_in_batches(embedder: Embedder, texts: list[str], batch_size: int = EMBEDDING_BATCH_SIZE) -> EmbeddingBatch:
    """Embed any number of texts with as few requests as possible: one request per batch_size texts.
    One request per chunk would multiply the round trips and use up the requests-per-minute limit
    long before the tokens-per-minute limit."""
    vectors: list[list[float]] = []
    total_tokens = 0
    for start in range(0, len(texts), batch_size):
        batch = embedder.embed(texts[start:start + batch_size])
        vectors += batch.vectors
        total_tokens += batch.total_tokens
    return EmbeddingBatch(vectors=vectors, total_tokens=total_tokens)


def openai_embedder_from_settings() -> OpenAIEmbedder:
    if not settings.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY is not set: add it to .env at the repository root (.env is not in git)")
    client = OpenAI(api_key=settings.openai_api_key, base_url=settings.openai_base_url, timeout=30,
                    max_retries=settings.openai_max_retries)
    return OpenAIEmbedder(client, settings.embedding_model)
