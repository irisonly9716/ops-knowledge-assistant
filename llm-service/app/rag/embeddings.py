"""Embeddings: turning text into vectors.

Callers (ingestion and retrieval, later) depend only on the small Embedder interface, not on
whether OpenAI or another model is behind it. Tests swap in a fake implementation: no network,
no cost. It is the same idea as the consumer taking its Kafka client as a parameter.

This module does one thing: one API call. Batching large inputs and rate limiting come when
the ingestion pipeline is wired in; the OpenAI SDK already retries network errors, 429s and
5xx responses (max_retries, with exponential backoff).
"""
from dataclasses import dataclass
from typing import Protocol

from openai import OpenAI

from app.config import settings

# doc_chunks.embedding is VECTOR(1536). A vector of another size can't be written to the
# database, so catch it here, with a clearer message about which step went wrong than the
# database's "expected 1536 dimensions"
EMBEDDING_DIMENSIONS = 1536


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


def openai_embedder_from_settings() -> OpenAIEmbedder:
    if not settings.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY is not set: add it to .env at the repository root (.env is not in git)")
    client = OpenAI(api_key=settings.openai_api_key, base_url=settings.openai_base_url, timeout=30)
    return OpenAIEmbedder(client, settings.embedding_model)
