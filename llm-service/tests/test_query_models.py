"""The async model calls with a fake OpenAI client: what is sent, and that retries stay inside the budget."""
from types import SimpleNamespace

import httpx
import openai
import pytest

from app.config import settings
from app.rag import query_models
from app.rag.embeddings import EMBEDDING_DIMENSIONS
from app.rag.query_models import OpenAIAnswerModel, OpenAIQueryEmbedder
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
    return OpenAIAnswerModel(SimpleNamespace(responses=api), "gpt-test", request_timeout, 800, sleep=sleep)


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
    embedder = OpenAIQueryEmbedder(SimpleNamespace(embeddings=api), "embed-test", request_timeout=3.0)
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
    embedder = OpenAIQueryEmbedder(SimpleNamespace(embeddings=api), "embed-test", request_timeout=3.0, sleep=sleep)
    await embedder.embed_question("Why?", TimeBudget(10))
    assert len(api.calls) == 2
    assert len(sleeps) == 1


def test_the_sdks_own_retries_are_off(monkeypatch):
    monkeypatch.setattr(settings, "openai_api_key", "test-key")
    assert query_models.openai_client_from_settings().max_retries == 0


def test_a_missing_key_says_where_to_put_it(monkeypatch):
    monkeypatch.setattr(settings, "openai_api_key", "")
    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        query_models.openai_client_from_settings()
