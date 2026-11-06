"""The TEI reranker client and its fallback, against httpx's mock transport: no server, no model."""
import asyncio
import json
import time

import httpx
import pytest

from app.rag.reranking import RerankFailed, TeiReranker, rerank_or_keep
from app.rag.retrieval import RetrievedChunk
from app.time_budget import TimeBudget


def chunk(number: int) -> RetrievedChunk:
    return RetrievedChunk(chunk_id=number, doc_id=number, doc_type="sop", title=f"Doc {number}", department="ALL",
                          heading_path=("Steps",), content=f"text {number}", score=0.5)


def reranker(handler, timeout: float = 1.0) -> TeiReranker:
    client = httpx.AsyncClient(base_url="http://reranker", transport=httpx.MockTransport(handler))
    return TeiReranker(client, timeout)


def ranks(*pairs):
    """TEI's answer: [{"index", "score"}, ...]."""
    return httpx.Response(200, json=[{"index": index, "score": score} for index, score in pairs])


# ---- the client


@pytest.mark.anyio
async def test_the_question_and_texts_go_to_rerank_and_the_order_follows_the_scores_not_the_answer_order():
    sent = []

    def handler(request):
        sent.append((request.url.path, json.loads(request.content)))
        return ranks((0, 0.1), (2, 0.9), (1, 0.5))   # not sorted, on purpose

    order = await reranker(handler).rerank("Why?", ["a", "b", "c"], TimeBudget(5))
    assert order == [2, 1, 0]
    assert sent == [("/rerank", {"query": "Why?", "texts": ["a", "b", "c"], "truncate": True})]


@pytest.mark.anyio
@pytest.mark.parametrize("answer", [
    ranks((0, 0.9)),                                   # one of two texts missing
    ranks((0, 0.9), (0, 0.8)),                         # one text twice
    ranks((0, 0.9), (5, 0.8)),                         # an index that doesn't exist
    httpx.Response(200, json={"ranks": []}),           # not a list of ranks
    httpx.Response(200, json=[{"index": 0}, {"index": 1}]),   # no scores
    httpx.Response(200, text="<html>proxy error</html>"),
])
async def test_an_answer_that_does_not_order_every_text_exactly_once_is_a_failure(answer):
    with pytest.raises(RerankFailed):
        await reranker(lambda request: answer).rerank("Why?", ["a", "b"], TimeBudget(5))


@pytest.mark.anyio
async def test_too_many_texts_for_the_server_is_a_failure_that_says_why():
    answer = httpx.Response(413, json={"error": "batch size 50 > maximum allowed batch size 32", "error_type": "validation"})
    with pytest.raises(RerankFailed, match="413: batch size 50 > maximum allowed batch size 32"):
        await reranker(lambda request: answer).rerank("Why?", ["a", "b"], TimeBudget(5))


@pytest.mark.anyio
async def test_a_slow_reranker_is_cut_off_at_its_limit():
    async def handler(request):
        await asyncio.sleep(5)
        return ranks((0, 1), (1, 0))

    started = time.perf_counter()
    with pytest.raises(TimeoutError):
        await reranker(handler, timeout=0.1).rerank("Why?", ["a", "b"], TimeBudget(5))
    assert time.perf_counter() - started < 1


@pytest.mark.anyio
async def test_the_limit_is_what_is_left_of_the_budget_when_that_is_less():
    async def handler(request):
        await asyncio.sleep(5)
        return ranks((0, 1), (1, 0))

    started = time.perf_counter()
    with pytest.raises(TimeoutError):
        await reranker(handler, timeout=10).rerank("Why?", ["a", "b"], TimeBudget(0.1))
    assert time.perf_counter() - started < 1


@pytest.mark.anyio
async def test_a_spent_budget_sends_nothing():
    sent = []
    with pytest.raises(TimeoutError):
        await reranker(lambda request: sent.append(request)).rerank("Why?", ["a", "b"], TimeBudget(0))
    assert sent == []


# ---- the fallback


class FakeReranker:
    def __init__(self, order=None, error=None):
        self.order, self.error, self.calls = order, error, []

    async def rerank(self, question, texts, budget):
        self.calls.append(texts)
        if self.error:
            raise self.error
        return self.order


@pytest.mark.anyio
async def test_reranked_chunks_come_back_in_the_rerankers_order():
    chunks = [chunk(1), chunk(2), chunk(3)]
    ranked, reranked = await rerank_or_keep(FakeReranker([2, 0, 1]), "Why?", chunks, TimeBudget(5))
    assert [c.chunk_id for c in ranked] == [3, 1, 2]
    assert reranked


@pytest.mark.anyio
@pytest.mark.parametrize("error", [TimeoutError(), httpx.ConnectError("refused"), RerankFailed("413: too many")])
async def test_a_failed_rerank_keeps_the_vector_order_and_logs_why_without_the_question(error, caplog):
    chunks = [chunk(1), chunk(2), chunk(3)]
    with caplog.at_level("WARNING", logger="query"):
        ranked, reranked = await rerank_or_keep(FakeReranker(error=error), "my private question", chunks, TimeBudget(5))
    assert (ranked, reranked) == (chunks, False)
    assert type(error).__name__ in caplog.text
    assert "my private question" not in caplog.text


@pytest.mark.anyio
async def test_nothing_to_reorder_asks_no_reranker():
    fake = FakeReranker([0])
    assert await rerank_or_keep(fake, "Why?", [chunk(1)], TimeBudget(5)) == ([chunk(1)], False)
    assert await rerank_or_keep(None, "Why?", [chunk(1), chunk(2)], TimeBudget(5)) == ([chunk(1), chunk(2)], False)
    assert fake.calls == []


@pytest.mark.anyio
async def test_the_whole_path_falls_back_when_the_server_is_unreachable():
    def handler(request):
        raise httpx.ConnectError("connection refused")

    chunks = [chunk(1), chunk(2)]
    assert await rerank_or_keep(reranker(handler), "Why?", chunks, TimeBudget(5)) == (chunks, False)
