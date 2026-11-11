"""The async model calls with a fake OpenAI client: what is sent, that retries stay inside the budget, and the limit on
calls at once."""
import asyncio
import time
from types import SimpleNamespace

import httpx
import openai
import pytest
from pydantic import BaseModel

from app.config import settings
from app.rag import query_models
from app.rag.embeddings import EMBEDDING_DIMENSIONS
from app.rag.query_models import (ModelBusy, ModelCallLimit, OpenAIAnswerModel, OpenAIQueryEmbedder,
                                  OpenAIStructuredModel)
from app.time_budget import TimeBudget


def status_error(status: int, retry_after: str | None = None) -> openai.APIStatusError:
    headers = {"retry-after": retry_after} if retry_after else {}
    response = httpx.Response(status, headers=headers, request=httpx.Request("POST", "https://api.test"))
    return openai.APIStatusError("failed", response=response, body=None)


def connection_error() -> openai.APIConnectionError:
    return openai.APIConnectionError(request=httpx.Request("POST", "https://api.test"))


class FakeApi:
    def __init__(self, result, errors=()):
        self.result = result
        self.errors = list(errors)
        self.calls = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.errors:
            raise self.errors.pop(0)
        return self.result


def response(text="Do this [1].", status="completed"):
    return SimpleNamespace(output_text=text, status=status, usage=SimpleNamespace(input_tokens=120, output_tokens=30))


def answer_model(api, sleeps=None, request_timeout=10.0):
    async def sleep(seconds):
        if sleeps is not None:
            sleeps.append(seconds)
    return OpenAIAnswerModel(SimpleNamespace(responses=api), "gpt-test", ModelCallLimit(50, 1.0), request_timeout, 800, sleep=sleep)


async def ask(model, budget=None):
    return await model.answer("instructions", "prompt", budget or TimeBudget(10))


@pytest.mark.anyio
async def test_an_answer_request_says_what_it_should():
    api = FakeApi(response())
    result = await ask(answer_model(api))
    sent = api.calls[0]
    assert (sent["model"], sent["instructions"], sent["input"], sent["max_output_tokens"]) == ("gpt-test", "instructions", "prompt", 800)
    assert sent["reasoning"] == {"effort": "none"}
    assert sent["store"] is False   # internal documents don't stay with the provider
    assert (result.text, result.complete, result.input_tokens, result.output_tokens) == ("Do this [1].", True, 120, 30)


@pytest.mark.anyio
async def test_a_reply_that_did_not_complete_says_so():
    assert (await ask(answer_model(FakeApi(response(status="incomplete"))))).complete is False


@pytest.mark.anyio
async def test_each_request_may_take_at_most_what_is_left_of_the_budget():
    api = FakeApi(response())
    await ask(answer_model(api, request_timeout=10.0), TimeBudget(3))
    assert 0 < api.calls[0]["timeout"] <= 3


@pytest.mark.anyio
async def test_the_request_timeout_applies_when_the_budget_is_larger():
    api = FakeApi(response())
    await ask(answer_model(api, request_timeout=2.0), TimeBudget(100))
    assert api.calls[0]["timeout"] == 2.0


@pytest.mark.anyio
@pytest.mark.parametrize("error", [connection_error(), status_error(500), status_error(503), status_error(408), status_error(429)])
async def test_temporary_failures_are_retried(error):
    sleeps = []
    api = FakeApi(response(), errors=[error])
    assert (await ask(answer_model(api, sleeps))).text == "Do this [1]."
    assert len(api.calls) == 2
    assert len(sleeps) == 1


@pytest.mark.anyio
async def test_the_servers_retry_after_is_honored():
    sleeps = []
    await ask(answer_model(FakeApi(response(), errors=[status_error(429, "2")]), sleeps))
    assert sleeps == [2.0]


@pytest.mark.anyio
@pytest.mark.parametrize("status", [400, 401, 403, 404])
async def test_failures_that_would_repeat_are_not_retried(status):
    api = FakeApi(response(), errors=[status_error(status)])
    with pytest.raises(openai.APIStatusError):
        await ask(answer_model(api))
    assert len(api.calls) == 1


@pytest.mark.anyio
async def test_a_wait_that_does_not_fit_the_budget_gives_up_at_once_with_the_real_error():
    sleeps = []
    api = FakeApi(response(), errors=[status_error(429, "120")])
    with pytest.raises(openai.APIStatusError):
        await ask(answer_model(api, sleeps), TimeBudget(5))
    assert sleeps == []
    assert len(api.calls) == 1


@pytest.mark.anyio
async def test_an_exhausted_budget_sends_nothing():
    api = FakeApi(response())
    with pytest.raises(TimeoutError):
        await ask(answer_model(api), TimeBudget(0))
    assert api.calls == []


@pytest.mark.anyio
async def test_a_question_is_embedded_with_the_same_settings_as_the_documents():
    data = SimpleNamespace(data=[SimpleNamespace(embedding=[0.5] * EMBEDDING_DIMENSIONS)])
    api = FakeApi(data)
    embedder = OpenAIQueryEmbedder(SimpleNamespace(embeddings=api), "embed-test", ModelCallLimit(50, 1.0), request_timeout=3.0)
    assert await embedder.embed_question("Why?", TimeBudget(10)) == [0.5] * EMBEDDING_DIMENSIONS
    sent = api.calls[0]
    assert (sent["model"], sent["input"], sent["dimensions"], sent["timeout"]) == ("embed-test", ["Why?"], EMBEDDING_DIMENSIONS, 3.0)


@pytest.mark.anyio
async def test_embedding_a_question_retries_inside_the_budget_too():
    data = SimpleNamespace(data=[SimpleNamespace(embedding=[0.5])])
    sleeps = []

    async def sleep(seconds):
        sleeps.append(seconds)
    api = FakeApi(data, errors=[connection_error()])
    embedder = OpenAIQueryEmbedder(SimpleNamespace(embeddings=api), "embed-test", ModelCallLimit(50, 1.0), request_timeout=3.0, sleep=sleep)
    await embedder.embed_question("Why?", TimeBudget(10))
    assert len(api.calls) == 2
    assert len(sleeps) == 1


class FakeParseApi(FakeApi):
    async def parse(self, **kwargs):
        return await self.create(**kwargs)


class Shape(BaseModel):
    route: str


def structured_model(api):
    return OpenAIStructuredModel(SimpleNamespace(responses=api), "gpt-test", ModelCallLimit(50, 1.0), 10.0, 200)


@pytest.mark.anyio
async def test_a_structured_request_asks_for_the_shape_without_reasoning_or_storing():
    parsed = Shape(route="data")
    api = FakeParseApi(SimpleNamespace(output_parsed=parsed, status="completed",
                                       usage=SimpleNamespace(input_tokens=300, output_tokens=20)))
    answer = await structured_model(api).ask("instructions", "the question", Shape, TimeBudget(10))
    sent = api.calls[0]
    assert sent["text_format"] is Shape and sent["input"] == "the question" and sent["instructions"] == "instructions"
    assert sent["reasoning"] == {"effort": "none"} and sent["store"] is False and sent["max_output_tokens"] == 200
    assert (answer.value, answer.input_tokens, answer.output_tokens) == (parsed, 300, 20)


@pytest.mark.anyio
async def test_a_structured_reply_that_did_not_complete_has_no_value():
    api = FakeParseApi(SimpleNamespace(output_parsed=Shape(route="da"), status="incomplete",
                                       usage=SimpleNamespace(input_tokens=300, output_tokens=200)))
    assert (await structured_model(api).ask("i", "q", Shape, TimeBudget(10))).value is None


def test_the_sdks_own_retries_are_off(monkeypatch):
    monkeypatch.setattr(settings, "openai_api_key", "test-key")
    assert query_models.openai_client_from_settings().max_retries == 0


def test_a_missing_key_says_where_to_put_it(monkeypatch):
    monkeypatch.setattr(settings, "openai_api_key", "")
    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        query_models.openai_client_from_settings()


# ---- the limit on calls at once


class SlowApi:
    """Takes a while to answer and records how many calls were running at the same time."""

    def __init__(self, seconds=0.05, errors=()):
        self.seconds = seconds
        self.errors = list(errors)
        self.running = 0
        self.peak = 0
        self.calls = 0

    async def create(self, **kwargs):
        self.calls += 1
        self.running += 1
        self.peak = max(self.peak, self.running)
        try:
            if self.errors:
                raise self.errors.pop(0)
            await asyncio.sleep(self.seconds)
            return response()
        finally:
            self.running -= 1


def limited_model(api, limit, sleep=asyncio.sleep):
    return OpenAIAnswerModel(SimpleNamespace(responses=api), "gpt-test", limit, 10.0, 800, sleep=sleep)


@pytest.mark.anyio
async def test_no_more_calls_run_at_once_than_the_limit():
    api = SlowApi()
    model = limited_model(api, ModelCallLimit(3, 5.0))
    await asyncio.gather(*(ask(model) for _ in range(12)))
    assert api.peak == 3
    assert api.calls == 12


@pytest.mark.anyio
async def test_below_the_limit_nothing_waits():
    api = SlowApi(seconds=0.05)
    model = limited_model(api, ModelCallLimit(10, 5.0))
    started = time.perf_counter()
    await asyncio.gather(*(ask(model) for _ in range(10)))
    assert time.perf_counter() - started < 0.4
    assert api.peak == 10


@pytest.mark.anyio
async def test_a_query_that_can_not_get_a_slot_soon_is_refused_instead_of_queueing_until_its_time_runs_out():
    api = SlowApi(seconds=5)
    model = limited_model(api, ModelCallLimit(1, 0.05))
    holder = asyncio.create_task(ask(model))
    await asyncio.sleep(0.01)
    started = time.perf_counter()
    with pytest.raises(ModelBusy):
        await ask(model)
    assert time.perf_counter() - started < 1
    assert api.calls == 1   # the refused one never reached the model
    holder.cancel()


@pytest.mark.anyio
async def test_the_wait_for_a_slot_never_outlasts_the_budget():
    api = SlowApi(seconds=5)
    model = limited_model(api, ModelCallLimit(1, 60.0))
    holder = asyncio.create_task(ask(model))
    await asyncio.sleep(0.01)
    started = time.perf_counter()
    with pytest.raises(ModelBusy):
        await ask(model, TimeBudget(0.05))
    assert time.perf_counter() - started < 1
    holder.cancel()


@pytest.mark.anyio
async def test_a_slot_comes_back_after_a_failure():
    api = SlowApi(errors=[status_error(400)])
    limit = ModelCallLimit(1, 0.05)
    with pytest.raises(openai.APIStatusError):
        await ask(limited_model(api, limit))
    assert (await ask(limited_model(api, limit))).text == "Do this [1]."


@pytest.mark.anyio
async def test_a_slot_comes_back_when_the_query_is_cancelled():
    api = SlowApi(seconds=5)
    limit = ModelCallLimit(1, 0.05)
    model = limited_model(api, limit)
    task = asyncio.create_task(ask(model))
    await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    api.seconds = 0
    assert (await ask(model)).text == "Do this [1]."


@pytest.mark.anyio
async def test_waiting_to_retry_holds_no_slot():
    """Otherwise a rate-limited provider would use up every slot with queries that are only sleeping."""
    limit = ModelCallLimit(1, 0.05)
    api = SlowApi(errors=[status_error(429, "1")])
    other = SlowApi(seconds=0)
    other_result = {}

    async def sleep_and_try_another_call(seconds):
        other_result["text"] = (await ask(limited_model(other, limit))).text
    model = limited_model(api, limit, sleep=sleep_and_try_another_call)
    assert (await ask(model)).text == "Do this [1]."
    assert other_result["text"] == "Do this [1]."


@pytest.mark.anyio
async def test_a_retry_counts_against_the_limit_like_any_call():
    api = SlowApi(errors=[status_error(500), status_error(500)])
    model = limited_model(api, ModelCallLimit(1, 5.0), sleep=lambda seconds: asyncio.sleep(0))
    await asyncio.gather(*(ask(model) for _ in range(3)))
    assert api.peak == 1


@pytest.mark.anyio
async def test_the_time_for_a_call_is_what_is_left_after_waiting_for_the_slot():
    seen = []

    class Recording(SlowApi):
        async def create(self, **kwargs):
            seen.append(kwargs["timeout"])
            return await super().create(**kwargs)
    api = Recording(seconds=0.2)
    model = limited_model(api, ModelCallLimit(1, 5.0))
    await asyncio.gather(ask(model, TimeBudget(3)), ask(model, TimeBudget(3)))
    assert seen[1] <= seen[0] - 0.15


@pytest.mark.anyio
async def test_embedding_and_answering_share_the_one_limit():
    limit = ModelCallLimit(1, 0.05)
    answers = SlowApi(seconds=5)
    holder = asyncio.create_task(ask(limited_model(answers, limit)))
    await asyncio.sleep(0.01)
    embedder = OpenAIQueryEmbedder(SimpleNamespace(embeddings=SlowApi()), "embed-test", limit, request_timeout=3.0)
    with pytest.raises(ModelBusy):
        await embedder.embed_question("Why?", TimeBudget(10))
    holder.cancel()


def test_the_client_can_keep_as_many_connections_as_the_limit_allows_calls(monkeypatch):
    monkeypatch.setattr(settings, "openai_api_key", "test-key")
    monkeypatch.setattr(settings, "model_max_concurrent_calls", 1500)
    pool = query_models.openai_client_from_settings()._client._transport._pool
    assert pool._max_connections == 1500       # the SDK's default of 1,000 would be a hidden limit at the 500 QPS target
    assert pool._max_keepalive_connections == 1500
