"""Tests for OpenAIEmbedder with a fake OpenAI client: no network, no cost."""
from types import SimpleNamespace

import openai
import pytest

from app.rag import embeddings
from app.rag.embeddings import EMBEDDING_DIMENSIONS, OpenAIEmbedder, embed_in_batches
from app.time_budget import TimeBudget


class FakeEmbeddingsApi:
    """Mimics client.embeddings: records each call's arguments and returns preset results."""

    def __init__(self, dims=EMBEDDING_DIMENSIONS, reverse=False, drop_last=False, tokens=7, errors=()):
        self.calls = []
        self.timeouts = []
        self.errors = list(errors)   # raised one per request before any success
        self.dims, self.reverse, self.drop_last, self.tokens = dims, reverse, drop_last, tokens

    def create(self, *, model, input, dimensions, timeout):
        self.calls.append({"model": model, "input": input, "dimensions": dimensions})
        self.timeouts.append(timeout)
        if self.errors:
            raise self.errors.pop(0)
        # the vector of the i-th text is all i, which makes the order easy to check
        data = [SimpleNamespace(index=i, embedding=[float(i)] * self.dims) for i in range(len(input))]
        if self.reverse:
            data.reverse()
        if self.drop_last:
            data.pop()
        return SimpleNamespace(data=data, usage=SimpleNamespace(total_tokens=self.tokens))


def make_embedder(**fake_options):
    api = FakeEmbeddingsApi(**fake_options)
    return OpenAIEmbedder(SimpleNamespace(embeddings=api), "text-embedding-3-small"), api


def test_returns_one_vector_per_text_and_the_billed_tokens():
    embedder, api = make_embedder(tokens=12)
    batch = embedder.embed(["Account restriction handling", "Our livestream reach was limited"])
    assert len(batch.vectors) == 2
    assert all(len(v) == EMBEDDING_DIMENSIONS for v in batch.vectors)
    assert batch.total_tokens == 12
    assert api.calls == [{"model": "text-embedding-3-small",
                          "input": ["Account restriction handling", "Our livestream reach was limited"],
                          "dimensions": EMBEDDING_DIMENSIONS}]


def test_vectors_follow_input_order_even_if_the_api_returns_them_shuffled():
    embedder, _ = make_embedder(reverse=True)
    batch = embedder.embed(["a", "b", "c"])
    assert [v[0] for v in batch.vectors] == [0.0, 1.0, 2.0]


def test_empty_input_makes_no_api_call():
    embedder, api = make_embedder()
    assert embedder.embed([]).vectors == []
    assert api.calls == []


@pytest.mark.parametrize("texts", [[""], ["fine", "   "]], ids=["empty string", "whitespace only"])
def test_blank_text_is_rejected_before_calling_the_api(texts):
    embedder, api = make_embedder()
    with pytest.raises(ValueError, match="empty text"):
        embedder.embed(texts)
    assert api.calls == []


def test_wrong_dimension_is_caught_before_it_reaches_the_database():
    embedder, _ = make_embedder(dims=3072)  # e.g. someone switched to 3-large and forgot to pass dimensions
    with pytest.raises(ValueError, match="3072-dimensional"):
        embedder.embed(["x"])


def test_missing_vectors_are_an_error_not_a_silent_misalignment():
    embedder, _ = make_embedder(drop_last=True)
    with pytest.raises(ValueError, match="received 1 vectors"):
        embedder.embed(["a", "b"])


def test_factory_refuses_to_start_without_an_api_key(monkeypatch):
    monkeypatch.setattr(embeddings.settings, "openai_api_key", "")
    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        embeddings.openai_embedder_from_settings()


def test_embed_in_batches_sends_one_request_per_batch_and_keeps_the_order():
    embedder, api = make_embedder(tokens=5)
    batch = embed_in_batches(embedder, [f"text {i}" for i in range(5)], batch_size=2)
    assert [call["input"] for call in api.calls] == [["text 0", "text 1"], ["text 2", "text 3"], ["text 4"]]
    assert [v[0] for v in batch.vectors] == [0.0, 1.0, 0.0, 1.0, 0.0]   # each batch's vectors in its own input order
    assert batch.total_tokens == 15                                     # billed tokens add up over the requests


# ------------------------------------------------------------------ retries and the time budget


class FakeTime:
    """A clock and a sleep that only move when told to, so nothing really waits."""

    def __init__(self):
        self.now = 1000.0
        self.sleeps = []

    def clock(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


def timeout_error():
    return openai.APITimeoutError(request=None)


def status_error(cls, status, headers=None):
    return cls("failed", response=SimpleNamespace(status_code=status, headers=headers or {}, request=None), body=None)


def retrying_embedder(api, fake_time, request_timeout=20.0):
    return OpenAIEmbedder(SimpleNamespace(embeddings=api), "text-embedding-3-small",
                          request_timeout=request_timeout, sleep=fake_time.sleep)


@pytest.mark.parametrize("error", [
    timeout_error(),
    openai.APIConnectionError(request=None),
    status_error(openai.RateLimitError, 429),
    status_error(openai.InternalServerError, 503),
], ids=["timeout", "connection", "429", "503"])
def test_temporary_errors_are_retried_with_backoff(error):
    fake_time = FakeTime()
    api = FakeEmbeddingsApi(errors=[error, error])
    batch = retrying_embedder(api, fake_time).embed(["a"], TimeBudget(100, clock=fake_time.clock))
    assert len(batch.vectors) == 1 and len(api.calls) == 3
    assert 0.375 <= fake_time.sleeps[0] <= 0.5 and 0.75 <= fake_time.sleeps[1] <= 1.0   # doubling, minus jitter


@pytest.mark.parametrize("error", [
    status_error(openai.BadRequestError, 400),
    status_error(openai.AuthenticationError, 401),
], ids=["400", "401"])
def test_errors_that_a_retry_cannot_fix_are_raised_at_once(error):
    fake_time = FakeTime()
    api = FakeEmbeddingsApi(errors=[error])
    with pytest.raises(type(error)):
        retrying_embedder(api, fake_time).embed(["a"], TimeBudget(100, clock=fake_time.clock))
    assert len(api.calls) == 1 and fake_time.sleeps == []


def test_retry_after_from_the_server_is_honored():
    fake_time = FakeTime()
    api = FakeEmbeddingsApi(errors=[status_error(openai.RateLimitError, 429, {"retry-after": "7"})])
    retrying_embedder(api, fake_time).embed(["a"], TimeBudget(100, clock=fake_time.clock))
    assert fake_time.sleeps == [7.0]


def test_a_retry_after_longer_than_the_budget_gives_up_instead_of_waiting():
    # The SDK would wait up to 120 s here, whatever the caller's deadline
    fake_time = FakeTime()
    api = FakeEmbeddingsApi(errors=[status_error(openai.RateLimitError, 429, {"retry-after": "120"})])
    with pytest.raises(openai.RateLimitError):
        retrying_embedder(api, fake_time).embed(["a"], TimeBudget(100, clock=fake_time.clock))
    assert fake_time.sleeps == []


def test_every_request_timing_out_stays_within_the_budget():
    # The worst case of issue #6: before the budget, this took about 590 s
    fake_time = FakeTime()
    start = fake_time.now

    class AlwaysHangs(FakeEmbeddingsApi):
        def create(self, *, model, input, dimensions, timeout):
            fake_time.now += timeout   # the request hangs until its timeout
            self.timeouts.append(timeout)
            raise timeout_error()

    api = AlwaysHangs()
    with pytest.raises(openai.APITimeoutError):
        retrying_embedder(api, fake_time).embed(["a"], TimeBudget(200, clock=fake_time.clock))
    assert fake_time.now - start <= 200
    assert all(t <= 20 for t in api.timeouts)


def test_the_last_request_only_gets_the_time_that_is_left():
    fake_time = FakeTime()
    api = FakeEmbeddingsApi()
    retrying_embedder(api, fake_time).embed(["a"], TimeBudget(5, clock=fake_time.clock))
    assert api.timeouts == [5]               # not the usual 20 s


def test_a_used_up_budget_sends_no_request():
    fake_time = FakeTime()
    api = FakeEmbeddingsApi()
    with pytest.raises(TimeoutError):
        retrying_embedder(api, fake_time).embed(["a"], TimeBudget(0, clock=fake_time.clock))
    assert api.calls == []


def test_all_batches_share_one_budget():
    fake_time = FakeTime()

    class SlowApi(FakeEmbeddingsApi):
        def create(self, *, model, input, dimensions, timeout):
            fake_time.now += 8
            return super().create(model=model, input=input, dimensions=dimensions, timeout=timeout)

    api = SlowApi()
    embed_in_batches(retrying_embedder(api, fake_time), ["a", "b", "c"], batch_size=1,
                     budget=TimeBudget(20, clock=fake_time.clock))
    assert api.timeouts == [20, 12, 4]   # each request takes 8 s, so later batches get what is left


def test_the_sdk_retries_are_switched_off(monkeypatch):
    # Otherwise the SDK's retries multiply with ours and ignore the budget
    monkeypatch.setattr(embeddings.settings, "openai_api_key", "not-a-real-key")
    assert embeddings.openai_embedder_from_settings()._client.max_retries == 0
