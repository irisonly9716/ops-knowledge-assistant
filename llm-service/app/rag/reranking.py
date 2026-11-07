"""Reordering the candidates of a search with a cross-encoder, before the prompt takes the first top_k.

Why: vector search scores a question and a chunk separately, then compares the two vectors. A cross-encoder reads the
question and a chunk together, which is slower and much better at telling which chunk answers. Measured on the
evaluation's dev questions with the top 50 candidates (docs/eval-report.md): every labelled section was among the 50,
but only 80.3% were in the first 5. bge-reranker-v2-m3 raised that to 92.7% and MRR from 0.743 to 0.913;
ms-marco-MiniLM-L6-v2, 25 times smaller, to 88.7% and 0.802, and it ranked paraphrased questions worse than vector
search did. A reranker can only reorder what the search found: a chunk outside the candidates stays lost.

Why a separate service: the model runs in Text Embeddings Inference (TEI), on a GPU in production, and this service sends
it one HTTP request per query. On a 6-core CPU it took 44 s for 50 candidates, so it can't run in this process: four
workers would each load 1.9 GB, and the computing would stop the event loop. Behind TEI it is one model on one GPU,
scaled apart from this service. Its latency on a GPU is not measured yet (#11).

Permissions: the reranker only ever gets chunks the search returned, and the search already applied the department
filter. It has no way to see anything else.

When it fails: a timeout, an error or an answer that doesn't hold is logged and the query goes on in vector order. A
reranker that is down makes answers worse, never absent.
"""
import asyncio
import logging
from typing import Protocol

import httpx

from app.config import settings
from app.rag.retrieval import RetrievedChunk
from app.time_budget import TimeBudget

log = logging.getLogger("query")


class RerankFailed(Exception):
    """The reranker answered, but not with an order that can be used."""


class Reranker(Protocol):
    async def rerank(self, question: str, texts: list[str], budget: TimeBudget) -> list[int]:
        """The indices of texts, best first: every index exactly once."""
        ...


class TeiReranker:
    """POST /rerank of Text Embeddings Inference: {"query", "texts"} in, [{"index", "score"}, ...] out.

    TEI accepts 32 texts a request unless started with a higher --max-client-batch-size (docker-compose.yml sets 64), and
    answers more with 413: every query would quietly fall back to vector order, so a 413 is logged with its reason."""

    def __init__(self, client: httpx.AsyncClient, timeout_seconds: float):
        self._client = client
        self._timeout = timeout_seconds

    async def rerank(self, question: str, texts: list[str], budget: TimeBudget) -> list[int]:
        timeout = min(self._timeout, budget.remaining())
        if timeout <= 0:
            raise TimeoutError("no time left to rerank")
        # One bound on the whole request, waiting for a pooled connection included: httpx's own timeouts are per step
        async with asyncio.timeout(timeout):
            response = await self._client.post("/rerank", json={"query": question, "texts": texts, "truncate": True})
        if response.status_code != 200:
            raise RerankFailed(f"{response.status_code}: {_error_of(response)}")
        try:
            ranks = [(float(item["score"]), int(item["index"])) for item in response.json()]
        except (ValueError, TypeError, KeyError) as error:   # JSON decoding errors are ValueErrors
            raise RerankFailed(f"unexpected answer: {error}") from error
        order = [index for _, index in sorted(ranks, key=lambda rank: -rank[0])]   # best first, whatever order it came in
        if sorted(order) != list(range(len(texts))):
            raise RerankFailed(f"the answer doesn't rank each of the {len(texts)} texts exactly once")
        return order

    async def aclose(self) -> None:
        await self._client.aclose()


def _error_of(response: httpx.Response) -> str:
    try:
        return str(response.json().get("error"))[:200]
    except ValueError:
        return response.text[:200]


async def rerank_or_keep(reranker: Reranker | None, question: str, chunks: list[RetrievedChunk],
                         budget: TimeBudget) -> tuple[list[RetrievedChunk], bool]:
    """The chunks in the reranker's order, or as they came when there is no reranker or it failed. The second value
    says which. Never raises for the reranker: a failed rerank costs answer quality, not the answer."""
    if reranker is None or len(chunks) < 2:
        return chunks, False
    try:
        order = await reranker.rerank(question, [chunk.content for chunk in chunks], budget)
    except (TimeoutError, httpx.HTTPError, RerankFailed) as error:
        # The question stays out of the log, as everywhere on the query path
        log.warning("rerank failed, vector order kept: %s %s", type(error).__name__, str(error)[:200])
        return chunks, False
    return [chunks[index] for index in order], True


def tei_reranker_from_settings(timeout_seconds: float | None = None) -> TeiReranker | None:
    """None when RERANKER_URL is empty: no reranking at all, and no warning on every query. `timeout_seconds` is for
    the evaluation, which waits for a reranker on a development CPU; the service uses the setting."""
    if not settings.reranker_url:
        return None
    timeout = timeout_seconds or settings.rerank_timeout_seconds
    # As many connections as model calls: a rerank holds one only while its request runs
    connections = settings.model_max_concurrent_calls
    client = httpx.AsyncClient(base_url=settings.reranker_url, timeout=timeout,
                               limits=httpx.Limits(max_connections=connections, max_keepalive_connections=connections))
    return TeiReranker(client, timeout)
