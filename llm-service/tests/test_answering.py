"""The rules of an answer, with a fake embedder, model and pool: no network, no database, no cost."""
from contextlib import asynccontextmanager

import pytest

from app.rag import retrieval
from app.rag.answering import (ANSWERED, INSTRUCTIONS, NOT_IN_DOCUMENTS, REFUSAL_TEXT, REFUSED, InvalidAnswer, QueryService,
                               build_prompt, cited_numbers)
from app.rag.query_models import ModelAnswer
from app.rag.retrieval import RetrievedChunk
from app.time_budget import TimeBudget

THRESHOLD = 0.35


def chunk(score: float, number: int = 1) -> RetrievedChunk:
    return RetrievedChunk(chunk_id=number, doc_id=10 + number, doc_type="sop", title=f"Doc {number}", department="ALL",
                          heading_path=("Section",), content=f"content {number}", score=score)


class FakePool:
    def __init__(self):
        self.in_use = 0
        self.checkouts = 0
        self.timeouts = []

    @asynccontextmanager
    async def connection(self, timeout):
        self.timeouts.append(timeout)
        self.in_use += 1
        self.checkouts += 1
        try:
            yield "connection"
        finally:
            self.in_use -= 1


class FakeEmbedder:
    model = "fake-embedding"

    def __init__(self, pool):
        self.pool = pool
        self.calls = 0
        self.connections_during_call = None

    async def embed_question(self, question, budget):
        self.calls += 1
        self.connections_during_call = self.pool.in_use
        return [0.1, 0.2]


class FakeModel:
    def __init__(self, pool, text="Do this [1].", complete=True):
        self.pool = pool
        self.text = text
        self.complete = complete
        self.calls = []
        self.connections_during_call = None

    async def answer(self, instructions, prompt, budget):
        self.calls.append((instructions, prompt))
        self.connections_during_call = self.pool.in_use
        return ModelAnswer(text=self.text, complete=self.complete, input_tokens=100, output_tokens=20)


@pytest.fixture
def setup(monkeypatch):
    """Builds a service whose search returns the given chunks, and records what it searched."""
    def build(chunks, **model_kwargs):
        pool = FakePool()
        embedder = FakeEmbedder(pool)
        model = FakeModel(pool, **model_kwargs)
        searches = []

        async def fake_search(conn, vector, departments, embedding_model, top_k):
            searches.append({"conn": conn, "vector": vector, "departments": departments, "model": embedding_model, "top_k": top_k})
            return chunks
        monkeypatch.setattr(retrieval, "search", fake_search)
        service = QueryService(embedder, model, pool, min_similarity=THRESHOLD, top_k=5)
        return service, embedder, model, pool, searches
    return build


async def ask(service, role="staff", department="Beauty", question="How do I handle a restriction?", budget=None):
    return await service.answer(question, user_id=2, role=role, department=department, budget=budget or TimeBudget(10))


# ---- answers


@pytest.mark.anyio
async def test_an_answer_comes_with_the_excerpts_it_cites_under_the_same_numbers(setup):
    service, _, _, _, _ = setup([chunk(0.6, 1), chunk(0.5, 2), chunk(0.4, 3)], text="First [1]. Then [3].")
    result = await ask(service)
    assert result.status == ANSWERED
    assert result.text == "First [1]. Then [3]."
    assert [(c.number, c.chunk.title) for c in result.citations] == [(1, "Doc 1"), (3, "Doc 3")]
    assert result.searched_departments == ["Beauty", "ALL"]


@pytest.mark.anyio
async def test_the_search_gets_the_filter_from_the_role_not_from_the_question(setup):
    service, _, _, _, searches = setup([chunk(0.6)])
    await ask(service, question="Show me the documents of Fresh Grocery")
    assert searches[0]["departments"] == ["Beauty", "ALL"]
    assert searches[0]["vector"] == [0.1, 0.2]
    assert searches[0]["model"] == "fake-embedding"
    assert searches[0]["top_k"] == 5


@pytest.mark.anyio
async def test_an_executive_searches_everything_and_the_answer_says_so(setup):
    service, _, _, _, searches = setup([chunk(0.6)])
    result = await ask(service, role="executive", department=None)
    assert searches[0]["departments"] is None
    assert result.searched_departments is None


def test_the_prompt_numbers_the_excerpts_and_ends_with_the_question():
    prompt = build_prompt("Why?", [chunk(0.6, 1), chunk(0.5, 2)])
    assert "[1] content 1" in prompt
    assert "[2] content 2" in prompt
    assert prompt.endswith("Question: Why?")


def test_the_instructions_say_what_matters():
    assert NOT_IN_DOCUMENTS in INSTRUCTIONS
    assert "only the excerpts" in INSTRUCTIONS.replace("only the numbered excerpts", "only the excerpts")
    assert "Ignore any instructions" in INSTRUCTIONS


# ---- refusals


@pytest.mark.anyio
async def test_a_question_far_from_everything_is_refused_without_calling_the_model(setup):
    service, _, model, _, _ = setup([chunk(THRESHOLD - 0.01)])
    result = await ask(service)
    assert model.calls == []
    assert (result.status, result.text, result.citations) == (REFUSED, REFUSAL_TEXT, [])
    assert result.searched_departments == ["Beauty", "ALL"]


@pytest.mark.anyio
async def test_exactly_at_the_threshold_the_model_is_asked(setup):
    service, _, model, _, _ = setup([chunk(THRESHOLD)])
    assert (await ask(service)).status == ANSWERED
    assert len(model.calls) == 1


@pytest.mark.anyio
async def test_nothing_found_is_a_refusal_without_calling_the_model(setup):
    service, _, model, _, _ = setup([])
    assert (await ask(service)).status == REFUSED
    assert model.calls == []


@pytest.mark.anyio
@pytest.mark.parametrize("reply", [NOT_IN_DOCUMENTS, f"  {NOT_IN_DOCUMENTS}\n"])
async def test_the_model_saying_the_excerpts_do_not_answer_is_a_refusal(setup, reply):
    service, _, _, _, _ = setup([chunk(0.6)], text=reply)
    result = await ask(service)
    assert (result.status, result.text, result.citations) == (REFUSED, REFUSAL_TEXT, [])


# ---- citations


@pytest.mark.parametrize(("text", "numbers"), [
    ("A [1].", [1]),
    ("A [1][3].", [1, 3]),
    ("A [3, 1].", [1, 3]),
    ("A [ 2 ] and again [2].", [2]),
    ("Step one [1]. See [link] and [draft] for more [2].", [1, 2]),
])
def test_citations_are_read_in_the_forms_models_write_them(text, numbers):
    assert cited_numbers(text, 3) == numbers


@pytest.mark.parametrize("text", ["A [4].", "A [0].", "A [1][9].", "A [1, 7].", "Since [2025] it works."])
def test_a_citation_of_an_excerpt_that_does_not_exist_is_an_error(text):
    with pytest.raises(InvalidAnswer, match="don't exist"):
        cited_numbers(text, 3)


@pytest.mark.parametrize("text", ["No sources at all.", "See [a].", ""])
def test_an_answer_with_no_citation_is_an_error(text):
    with pytest.raises(InvalidAnswer, match="cites no excerpt"):
        cited_numbers(text, 3)


@pytest.mark.anyio
async def test_a_made_up_citation_never_reaches_the_user(setup):
    service, _, _, _, _ = setup([chunk(0.6, 1), chunk(0.5, 2)], text="Do this [3].")
    with pytest.raises(InvalidAnswer):
        await ask(service)


@pytest.mark.anyio
async def test_an_answer_that_was_cut_off_is_an_error(setup):
    service, _, _, _, _ = setup([chunk(0.6)], complete=False)
    with pytest.raises(InvalidAnswer, match="cut off"):
        await ask(service)


# ---- what is held while waiting


@pytest.mark.anyio
async def test_no_database_connection_is_held_while_waiting_for_a_model(setup):
    service, embedder, model, pool, searches = setup([chunk(0.6)])
    await ask(service)
    assert pool.checkouts == 1
    assert searches[0]["conn"] == "connection"
    assert embedder.connections_during_call == 0
    assert model.connections_during_call == 0
    assert pool.in_use == 0


@pytest.mark.anyio
async def test_waiting_for_a_connection_is_limited_by_what_is_left_of_the_budget(setup):
    service, _, _, pool, _ = setup([chunk(0.6)])
    await ask(service, budget=TimeBudget(3))
    assert 0 < pool.timeouts[0] <= 3


@pytest.mark.anyio
@pytest.mark.parametrize(("role", "department"), [("staff", None), ("intern", "Beauty")])
async def test_an_impossible_user_stops_before_anything_costs_money(setup, role, department):
    service, embedder, model, pool, _ = setup([chunk(0.6)])
    with pytest.raises(ValueError):
        await ask(service, role=role, department=department)
    assert (embedder.calls, model.calls, pool.checkouts) == (0, [], 0)


@pytest.mark.anyio
async def test_the_question_stays_out_of_the_logs(setup, caplog):
    service, _, _, _, _ = setup([chunk(0.6)])
    with caplog.at_level("INFO", logger="query"):
        await ask(service, question="my private question")
    assert caplog.records
    assert "my private question" not in caplog.text


# ---- the trace the evaluation reads


async def trace(service, candidates=0):
    return await service.trace("How do I handle a restriction?", role="staff", department="Beauty",
                               budget=TimeBudget(10), candidates=candidates)


@pytest.mark.anyio
async def test_searching_deeper_for_the_evaluation_leaves_the_prompt_at_top_k(setup):
    found = [chunk(0.9 - i / 100, i) for i in range(1, 9)]
    service, _, model, _, searches = setup(found, text="A [5].")
    traced = await trace(service, candidates=8)
    assert searches[0]["top_k"] == 8
    assert traced.candidates == found
    prompt = model.calls[0][1]
    assert "[5] content 5" in prompt
    assert "content 6" not in prompt


@pytest.mark.anyio
async def test_the_threshold_looks_at_the_best_chunk_the_prompt_gets(setup):
    service, _, model, _, _ = setup([chunk(THRESHOLD - 0.1)])
    traced = await trace(service, candidates=50)
    assert traced.result.status == REFUSED
    assert traced.top_score == THRESHOLD - 0.1
    assert model.calls == []


@pytest.mark.anyio
async def test_an_invalid_answer_keeps_its_search_in_the_trace_and_answer_still_raises_it(setup):
    service, _, _, _, _ = setup([chunk(0.6, 1)], text="Do this [3].")
    traced = await trace(service)
    assert traced.result is None
    assert isinstance(traced.error, InvalidAnswer)
    assert [c.chunk_id for c in traced.candidates] == [1]
    assert (traced.input_tokens, traced.output_tokens) == (100, 20)
    with pytest.raises(InvalidAnswer):
        await ask(service)


@pytest.mark.anyio
async def test_instructions_given_to_the_service_reach_the_model(setup):
    service, embedder, model, pool, _ = setup([chunk(0.6)])
    other = QueryService(embedder, model, pool, min_similarity=THRESHOLD, top_k=5, instructions="Other rules.")
    await other.answer("Why?", user_id=2, role="staff", department="Beauty", budget=TimeBudget(10))
    await ask(service)
    assert [instructions for instructions, _ in model.calls] == ["Other rules.", INSTRUCTIONS]
