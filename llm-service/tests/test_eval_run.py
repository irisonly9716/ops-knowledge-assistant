"""The evaluation run around the query path: what it records for a question and which questions each number uses.
A fake service stands in for the query path: no database, no model."""
import pytest

from app.rag.answering import ANSWERED, InvalidAnswer, QueryAnswer, QueryTrace
from app.rag.retrieval import RetrievedChunk
from evaluation import run
from evaluation.dataset import Asker, DocumentRef, Question, Section


def section(title: str, department: str = "Beauty") -> Section:
    return Section(document=DocumentRef(doc_type="sop", department=department, title=title), heading_path=("Steps",))


def question(id="q-1", route="documents", expected="answer", tags=("direct",), relevant=(), hidden=()) -> Question:
    # model_construct: the split rule and the other checks are the dataset's tests, not these
    return Question.model_construct(id=id, question="How?", turns=(), asker=Asker(department="Beauty", role="staff"),
                                    route=route, expected=expected, relevant=tuple(relevant), hidden=tuple(hidden),
                                    related=(), answer_points=("p",), tags=tuple(tags), source="human", group="g",
                                    split="dev")


def chunk(chunk_id: int, score: float = 0.5) -> RetrievedChunk:
    return RetrievedChunk(chunk_id=chunk_id, doc_id=1, doc_type="sop", title="T", department="Beauty",
                          heading_path=("Steps",), content=f"c{chunk_id}", score=score)


class FakeService:
    def __init__(self, traced=None, error=None):
        self.traced, self.error, self.calls = traced, error, []

    async def trace(self, question, *, role, department, budget, candidates):
        self.calls.append((question, role, department, candidates))
        if self.error:
            raise self.error
        return self.traced


class FakeEncoding:
    def encode(self, text):
        return text.split()


def traced(candidates, result=None, error=None) -> QueryTrace:
    return QueryTrace(result, error, False, candidates, candidates[0].score if candidates else 0.0, 1.0, 2.0, 3.0, 10, 5)


@pytest.mark.anyio
async def test_a_question_records_where_its_sections_ranked_among_fifty_and_what_the_prompt_got():
    relevant, other = section("Refunds"), section("Returns")
    found = [chunk(i) for i in range(1, 9)]
    service = FakeService(traced(found, QueryAnswer(ANSWERED, "A [1].", [], ["Beauty", "ALL"])))
    record = await run.ask_one(service, question(relevant=[relevant, other]), {relevant: [7, 2], other: [99]},
                               FakeEncoding())
    assert service.calls == [("How?", "staff", "Beauty", run.CANDIDATES)]
    assert record["section_ranks"] == [2, None]
    assert len(record["excerpts"]) == run.settings.query_top_k
    assert record["status"] == ANSWERED


@pytest.mark.anyio
async def test_a_hidden_section_among_the_candidates_is_counted_even_below_the_prompt():
    hidden = section("Fresh pricing", department="Fresh Grocery")
    service = FakeService(traced([chunk(i) for i in range(1, 9)], QueryAnswer("refused", "no", [], None)))
    record = await run.ask_one(service, question(expected="refuse", hidden=[hidden]), {hidden: [8]}, FakeEncoding())
    assert record["hidden_retrieved"] == 1


@pytest.mark.anyio
async def test_an_invalid_answer_is_an_error_that_keeps_its_retrieval():
    relevant = section("Refunds")
    service = FakeService(traced([chunk(1)], error=InvalidAnswer("cites nothing")))
    record = await run.ask_one(service, question(relevant=[relevant]), {relevant: [1]}, FakeEncoding())
    assert (record["status"], record["section_ranks"]) == ("error", [1])


@pytest.mark.anyio
async def test_a_timeout_is_an_error_and_the_report_still_builds():
    q = question()
    record = await run.ask_one(FakeService(error=TimeoutError()), q, {}, FakeEncoding())
    assert record["status"] == "error" and "embed_ms" not in record
    report = run.summarize([record], {q.id: q}, "t", "dev", "rules", None, 1.0)
    assert "1 ended in an error" in report


def test_questions_about_numbers_are_neither_graded_nor_in_the_threshold_sweep():
    assert run.graded_for_answer(question(route="documents"))
    assert not run.graded_for_answer(question(route="both"))
    assert not run.scored_for_refusal(question(route="data"))
    assert run.scored_for_refusal(question(route="data", expected="refuse"))


def test_the_report_names_questions_whose_hidden_sections_were_retrieved():
    q = question(id="q-leak", expected="refuse", tags=("unauthorized",))
    record = {"id": "q-leak", "question": "How?", "asker": {"role": "staff", "department": "Beauty"}, "route": "documents",
              "expected": "refuse", "tags": ["unauthorized"], "status": "refused", "top_score": 0.5, "section_ranks": [],
              "hidden_retrieved": 2, "embed_ms": 1, "search_ms": 1, "model_ms": 1, "input_tokens": 1,
              "output_tokens": 1, "embedding_tokens": 1}
    report = run.summarize([record], {q.id: q}, "t", "dev", "rules", None, 1.0)
    assert "among the candidates: 1 questions (q-leak)" in report
