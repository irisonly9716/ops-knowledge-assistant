"""POST /query through the real FastAPI app with a fake service: who may call it, what it accepts, how failures
look. TestClient isn't used as a context manager, so the app's startup (real pool, real client) doesn't run."""
import asyncio
import json
import sys
from pathlib import Path

import httpx
import openai
import psycopg
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app import db, server
from app.config import settings
from app.main import app, lifespan
from app.query_api import MAX_QUESTION_CHARS, QueryRequest, QueryResponse
from app.rag.answering import ANSWERED, REFUSAL_TEXT, REFUSED, Citation, InvalidAnswer, QueryAnswer
from app.rag.retrieval import RetrievedChunk

TOKEN = "test-internal-token"
HEADERS = {"X-Internal-Token": TOKEN}
CONTRACT = json.loads((Path(__file__).resolve().parents[2] / "contracts" / "query.examples.json").read_text(encoding="utf-8"))
CHUNK = RetrievedChunk(chunk_id=7, doc_id=3, doc_type="sop", title="Account Restriction Handling", department="ALL",
                       heading_path=("Account Restriction Handling", "Steps"), content="...", score=0.51834)


class FakeService:
    def __init__(self, result=None, error=None, delay=0.0):
        self.result = result or QueryAnswer(ANSWERED, "Do this [1].", [Citation(1, CHUNK)], ["Beauty", "ALL"])
        self.error = error
        self.delay = delay
        self.calls = []

    async def answer(self, question, *, user_id, role, department, budget):
        self.calls.append({"question": question, "user_id": user_id, "role": role, "department": department, "budget": budget})
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error:
            raise self.error
        return self.result


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(settings, "internal_service_token", TOKEN)
    app.state.query_service = FakeService()
    return TestClient(app, raise_server_exceptions=False)


def body(**overrides):
    return {"question": "How do I handle a restriction?", "userId": 2, "role": "staff", "department": "Beauty", **overrides}


# ---- who may call it


@pytest.mark.parametrize("headers", [{}, {"X-Internal-Token": "wrong"}, {"X-Internal-Token": ""}, {"Authorization": f"Bearer {TOKEN}"}])
def test_only_the_backends_token_gets_in(client, headers):
    r = client.post("/query", json=body(), headers=headers)
    assert r.status_code == 401
    assert r.json() == {"error": "this endpoint only accepts calls from the backend"}
    assert app.state.query_service.calls == []


def test_an_unset_token_lets_nobody_in_not_even_with_an_empty_header(client, monkeypatch):
    monkeypatch.setattr(settings, "internal_service_token", "")
    assert client.post("/query", json=body(), headers={"X-Internal-Token": ""}).status_code == 401
    assert client.post("/query", json=body()).status_code == 401


def test_without_the_token_nothing_about_the_request_is_examined(client):
    assert client.post("/query", json={"nonsense": True}).status_code == 401


def test_the_token_is_compared_as_bytes_so_any_header_value_is_a_plain_refusal(client):
    assert client.post("/query", json=body(), headers={"X-Internal-Token": "café".encode("latin-1")}).status_code == 401


def test_health_needs_no_token(client):
    assert client.get("/health").status_code == 200


# ---- what it accepts


def test_the_role_and_department_reach_the_service_as_sent(client):
    assert client.post("/query", json=body(), headers=HEADERS).status_code == 200
    call = app.state.query_service.calls[0]
    assert (call["question"], call["user_id"], call["role"], call["department"]) == ("How do I handle a restriction?", 2, "staff", "Beauty")


@pytest.mark.parametrize("overrides", [
    {"role": "intern"},
    {"role": "staff", "department": None},
    {"role": "manager", "department": ""},
    {"role": "executive", "department": "Beauty"},
    {"role": "admin", "department": "Beauty"},
    {"question": ""},
    {"question": "   "},
    {"question": "x" * (MAX_QUESTION_CHARS + 1)},
    {"userId": "two"},
    {"extra": "field"},
])
def test_a_malformed_request_is_a_400_with_a_reason_and_goes_no_further(client, overrides):
    r = client.post("/query", json=body(**overrides), headers=HEADERS)
    assert r.status_code == 400
    assert list(r.json()) == ["error"]
    assert r.json()["error"]
    assert app.state.query_service.calls == []


@pytest.mark.parametrize("missing", ["question", "userId", "role", "department"])
def test_every_field_is_required(client, missing):
    request = body()
    del request[missing]
    assert client.post("/query", json=request, headers=HEADERS).status_code == 400


def test_an_error_about_the_question_does_not_repeat_the_question(client):
    r = client.post("/query", json=body(question="s3cret " * 400), headers=HEADERS)
    assert r.status_code == 400
    assert "s3cret" not in r.text


def test_the_longest_allowed_question_is_accepted(client):
    assert client.post("/query", json=body(question="x" * MAX_QUESTION_CHARS), headers=HEADERS).status_code == 200


def test_executive_and_admin_come_without_a_department(client):
    assert client.post("/query", json=body(role="executive", department=None), headers=HEADERS).status_code == 200


# ---- what it answers


def test_an_answer_uses_the_field_names_of_the_contract(client):
    assert client.post("/query", json=body(), headers=HEADERS).json() == CONTRACT["responses"]["answered"]


def test_a_refusal_is_a_normal_answer_with_no_citations(client):
    app.state.query_service = FakeService(QueryAnswer(REFUSED, REFUSAL_TEXT, [], ["Beauty", "ALL"]))
    r = client.post("/query", json=body(), headers=HEADERS)
    assert r.status_code == 200
    assert r.json() == CONTRACT["responses"]["refused"]


def test_searching_everything_is_a_null_scope(client):
    app.state.query_service = FakeService(QueryAnswer(REFUSED, REFUSAL_TEXT, [], None))
    assert client.post("/query", json=body(role="admin", department=None), headers=HEADERS).json()["searchedDepartments"] is None


@pytest.mark.parametrize("example", CONTRACT["requests"])
def test_every_request_in_the_contract_is_accepted(example):
    QueryRequest.model_validate(example)


@pytest.mark.parametrize("name", CONTRACT["responses"])
def test_every_response_in_the_contract_round_trips(name):
    example = CONTRACT["responses"][name]
    assert QueryResponse.model_validate(example).model_dump(by_alias=True) == example


# ---- how failures look


REQUEST = httpx.Request("POST", "https://api.test")


@pytest.mark.parametrize(("error", "status"), [
    (TimeoutError("budget"), 504),
    (openai.APITimeoutError(REQUEST), 504),
    (InvalidAnswer("cites [9]"), 502),
    (openai.RateLimitError("slow down", response=httpx.Response(429, request=REQUEST), body=None), 503),
    (openai.APIConnectionError(request=REQUEST), 502),
    (openai.APIStatusError("bad", response=httpx.Response(500, request=REQUEST), body=None), 502),
    (psycopg.OperationalError("down"), 503),
    (RuntimeError("bug"), 500),
])
def test_each_failure_has_its_status_and_the_same_shape_without_details(client, error, status):
    app.state.query_service = FakeService(error=error)
    r = client.post("/query", json=body(), headers=HEADERS)
    assert r.status_code == status
    assert list(r.json()) == ["error"]
    assert "cites [9]" not in r.text
    assert "bug" not in r.text


def test_the_pools_timeout_counts_as_the_search_being_unavailable(client):
    from psycopg_pool import PoolTimeout
    app.state.query_service = FakeService(error=PoolTimeout("no connection"))
    assert client.post("/query", json=body(), headers=HEADERS).status_code == 503


def test_a_query_is_stopped_when_its_time_is_up_even_if_no_layer_checks(client, monkeypatch):
    monkeypatch.setattr(settings, "query_budget_seconds", 0.05)
    app.state.query_service = FakeService(delay=5)
    r = client.post("/query", json=body(), headers=HEADERS)
    assert r.status_code == 504
    assert "0.05" in r.json()["error"]


def test_the_service_gets_a_budget_of_the_configured_length(client):
    client.post("/query", json=body(), headers=HEADERS)
    remaining = app.state.query_service.calls[0]["budget"].remaining()
    assert 0 < remaining <= settings.query_budget_seconds


# ---- starting up


@pytest.mark.skipif(sys.platform != "win32", reason="only Windows has ProactorEventLoop")
def test_the_default_windows_event_loop_is_refused_with_advice():
    async def check():
        with pytest.raises(RuntimeError, match="app.server"):
            db.require_compatible_event_loop()
    asyncio.run(check(), loop_factory=asyncio.ProactorEventLoop)


def test_the_selector_event_loop_is_fine():
    async def check():
        db.require_compatible_event_loop()
    asyncio.run(check(), loop_factory=asyncio.SelectorEventLoop)


@pytest.mark.skipif(sys.platform != "win32", reason="only Windows has ProactorEventLoop")
def test_the_entry_point_chooses_the_event_loop_before_uvicorn_starts(monkeypatch):
    seen = {}

    def fake_run(target, **kwargs):
        seen.update(kwargs, target=target, policy=type(asyncio.get_event_loop_policy()).__name__)
    previous = asyncio.get_event_loop_policy()
    monkeypatch.setattr(server.uvicorn, "run", fake_run)
    monkeypatch.setattr(sys, "argv", ["app.server"])
    try:
        server.main()
    finally:
        asyncio.set_event_loop_policy(previous)
    assert seen["policy"] == "WindowsSelectorEventLoopPolicy"
    assert seen["target"] == "app.main:app"
    assert seen["reload"] is False


def test_the_service_listens_on_localhost_unless_told_otherwise():
    assert type(settings).model_fields["llm_service_host"].default == "127.0.0.1"


@pytest.mark.anyio
async def test_it_refuses_to_start_without_the_internal_token(monkeypatch):
    monkeypatch.setattr(settings, "internal_service_token", "")
    with pytest.raises(RuntimeError, match="INTERNAL_SERVICE_TOKEN"):
        async with lifespan(app):
            pass


def test_the_contract_has_the_cases_it_should():
    assert set(CONTRACT["responses"]) == {"answered", "refused"}
    with pytest.raises(ValidationError):
        QueryRequest.model_validate({**CONTRACT["requests"][0], "role": "intern"})
