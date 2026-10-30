"""Tests for OpenAIEmbedder with a fake OpenAI client: no network, no cost."""
from types import SimpleNamespace

import pytest

from app.rag import embeddings
from app.rag.embeddings import EMBEDDING_DIMENSIONS, OpenAIEmbedder


class FakeEmbeddingsApi:
    """Mimics client.embeddings: records each call's arguments and returns preset results."""

    def __init__(self, dims=EMBEDDING_DIMENSIONS, reverse=False, drop_last=False, tokens=7):
        self.calls = []
        self.dims, self.reverse, self.drop_last, self.tokens = dims, reverse, drop_last, tokens

    def create(self, *, model, input, dimensions):
        self.calls.append({"model": model, "input": input, "dimensions": dimensions})
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
