"""POST /query through the real FastAPI app with a fake service and a fake audit log: who may call it, what it
accepts, how failures look, and that every question it takes in is recorded. TestClient isn't used as a context
manager, so the app's startup (real pool, real client) doesn't run."""
import asyncio
import json
from datetime import date
import sys
from pathlib import Path
from uuid import UUID

import httpx
import openai
import psycopg
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app import audit, db, query_api, server
from app.audit import AuditLog
from app.config import settings
from app.main import app, lifespan
from app.query_api import MAX_QUESTION_CHARS, QueryRequest, QueryResponse
from app.query_flow import FlowAnswer, FlowTrace
from app.rag.answering import ANSWERED, REFUSAL_TEXT, REFUSED, Citation, InvalidAnswer
from app.rag.query_models import ModelBusy
from app.rag.retrieval import RetrievedChunk
from app.sql_agent.campaigns import Campaign
from app.sql_agent.execute import QueryResult
from app.sql_agent.pipeline import FAILED, FAILED_TEXT

TOKEN = "test-internal-token"
HEADERS = {"X-Internal-Token": TOKEN}
CONTRACT = json.loads((Path(__file__).resolve().parents[2] / "contracts" / "query.examples.json").read_text(encoding="utf-8"))
QUERY_ID = UUID(CONTRACT["responses"]["answered"]["queryId"])
CHUNK = RetrievedChunk(chunk_id=7, doc_id=3, doc_type="sop", title="Account Restriction Handling", department="ALL",
                       heading_path=("Account Restriction Handling", "Steps"), content="...", score=0.51834)


def from_documents(status, text, citations, searched=("Beauty", "ALL")) -> FlowAnswer:
    return FlowAnswer(status, text, citations, None if searched is None else list(searched), ("documents",), None, None)


def traced(answer: FlowAnswer | None, error: InvalidAnswer | None = None) -> FlowTrace:
    return FlowTrace(answer, error, None, "documents", None, None, (), 0.0, 0.0, 0.0, 0.0, (0, 0), 0.0)


class FakeService:
    """error: raised from answer(); invalid: an answer that failed validation, which the flow returns in its trace."""

    def __init__(self, result=None, error=None, delay=0.0, invalid=None, events=None):
        self.result = result or from_documents(ANSWERED, "Do this [1].", [Citation(1, CHUNK)])
        self.error, self.delay, self.invalid = error, delay, invalid
        self.calls, self.remembered = [], []
        self.events = [] if events is None else events

    async def answer(self, question, *, user_id, role, department, budget, session_id=None, query_id="-"):
        self.calls.append({"question": question, "user_id": user_id, "role": role, "department": department, "budget": budget,
                           "session_id": session_id, "query_id": query_id})
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error:
            raise self.error
        return traced(None, self.invalid) if self.invalid else traced(self.result)

    async def remember(self, user_id, session_id, question, answer):
        self.events.append("remember")
        if session_id:
            self.remembered.append((user_id, session_id, question, answer.text))


class FakeAudit(AuditLog):
    def __init__(self, error=None, events=None):
        super().__init__(None, 0.5)
        self.records, self.error = [], error
        self.events = [] if events is None else events

    async def write(self, record):
        self.events.append("write")
        if self.error:
            raise type(self.error)(*self.error.args)
        self.records.append(record)


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(settings, "internal_service_token", TOKEN)
    monkeypatch.setattr(query_api, "uuid4", lambda: QUERY_ID)   # the contract's examples carry one
    app.state.query_flow = FakeService()
    app.state.audit_log = FakeAudit()
    return TestClient(app, raise_server_exceptions=False)


def body(**overrides):
    return {"question": "How do I handle a restriction?", "userId": 2, "role": "staff", "department": "Beauty", **overrides}


# ---- who may call it


@pytest.mark.parametrize("headers", [{}, {"X-Internal-Token": "wrong"}, {"X-Internal-Token": ""}, {"Authorization": f"Bearer {TOKEN}"}])
def test_only_the_backends_token_gets_in(client, headers):
    r = client.post("/query", json=body(), headers=headers)
    assert r.status_code == 401
    assert r.json() == {"error": "this endpoint only accepts calls from the backend"}
    assert app.state.query_flow.calls == [] and app.state.audit_log.records == []


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
    call = app.state.query_flow.calls[0]
    assert (call["question"], call["user_id"], call["role"], call["department"]) == ("How do I handle a restriction?", 2, "staff", "Beauty")
    assert call["session_id"] is None   # no conversation: a question on its own


def test_the_conversation_reaches_the_service_as_a_uuid_and_anything_else_is_a_400(client):
    session = "5F0C1C9E-3C4E-4A3E-9D4E-2F1B7A6C8D90"
    assert client.post("/query", json=body(sessionId=session), headers=HEADERS).status_code == 200
    assert app.state.query_flow.calls[0]["session_id"] == session.lower()
    r = client.post("/query", json=body(sessionId="memory:3:someone-elses"), headers=HEADERS)
    assert r.status_code == 400 and "sessionId" in r.json()["error"] and len(app.state.query_flow.calls) == 1


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
    assert app.state.query_flow.calls == [] and app.state.audit_log.records == []


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
    app.state.query_flow = FakeService(from_documents(REFUSED, REFUSAL_TEXT, []))
    r = client.post("/query", json=body(), headers=HEADERS)
    assert r.status_code == 200
    assert r.json() == CONTRACT["responses"]["refused"]


def test_an_answer_from_the_data_carries_its_query_and_every_row_as_text(client):
    example = CONTRACT["responses"]["answeredFromData"]
    data = example["data"]
    app.state.query_flow = FakeService(FlowAnswer(
        ANSWERED, example["answer"], [], ["Consumer Electronics"], ("data",), data["sql"],
        QueryResult(tuple(data["columns"]), tuple(tuple(row) for row in data["rows"]), truncated=False)))
    assert client.post("/query", json=body(), headers=HEADERS).json() == example


def test_an_answer_from_both_has_citations_and_rows_and_a_null_value_stays_null(client):
    example = CONTRACT["responses"]["answeredFromBoth"]
    cited = example["citations"][0]
    chunk = RetrievedChunk(chunk_id=9, doc_id=cited["docId"], doc_type=cited["docType"], title=cited["title"],
                           department=cited["department"], heading_path=tuple(cited["headingPath"]), content="...",
                           score=cited["score"])
    data = example["data"]
    rows = tuple(tuple(row) for row in data["rows"]) + (("Electronics New Product Launch", "2025-09-30", None),)
    app.state.query_flow = FakeService(FlowAnswer(
        ANSWERED, example["answer"], [Citation(1, chunk)], ["Consumer Electronics", "ALL"], ("documents", "data"),
        data["sql"], QueryResult(tuple(data["columns"]), rows, truncated=True)))
    answered = client.post("/query", json=body(), headers=HEADERS).json()
    assert answered["data"]["rows"][1] == ["Electronics New Product Launch", "2025-09-30", None]
    assert answered["data"]["truncated"] is True
    answered["data"] |= {"rows": answered["data"]["rows"][:1], "truncated": False}
    assert answered == example


def test_a_question_no_query_could_be_written_for_is_an_answer_that_says_so_not_an_error(client):
    app.state.query_flow = FakeService(FlowAnswer(FAILED, FAILED_TEXT, [], ["Consumer Electronics"], ("data",), None,
                                                  None))
    r = client.post("/query", json=body(), headers=HEADERS)
    assert r.status_code == 200 and r.json() == CONTRACT["responses"]["failed"]


def campaign_of(example: dict) -> Campaign:
    return Campaign(1, example["campaignName"], example["department"], date.fromisoformat(example["startDate"]),
                    date.fromisoformat(example["endDate"]))


def test_an_answer_about_the_last_campaign_carries_the_campaign_it_turned_out_to_be(client):
    example = CONTRACT["responses"]["answeredAboutTheLastCampaign"]
    data = example["data"]
    app.state.query_flow = FakeService(FlowAnswer(
        ANSWERED, example["answer"], [], ["Beauty"], ("data",), data["sql"],
        QueryResult(tuple(data["columns"]), tuple(tuple(row) for row in data["rows"]), truncated=False),
        campaign_of(example["basis"])))
    assert client.post("/query", json=body(), headers=HEADERS).json() == example


def test_a_question_asked_back_lists_its_candidates_and_ran_no_path(client):
    example = CONTRACT["responses"]["clarify"]
    app.state.query_flow = FakeService(FlowAnswer(
        "clarify", example["answer"], [], None, (), None, None, None,
        tuple(campaign_of(c) for c in example["candidates"])))
    r = client.post("/query", json=body(role="executive", department=None), headers=HEADERS)
    assert r.status_code == 200 and r.json() == example


def test_searching_everything_is_a_null_scope(client):
    app.state.query_flow = FakeService(from_documents(REFUSED, REFUSAL_TEXT, [], searched=None))
    assert client.post("/query", json=body(role="admin", department=None), headers=HEADERS).json()["searchedDepartments"] is None


@pytest.mark.parametrize("example", CONTRACT["requests"])
def test_every_request_in_the_contract_is_accepted(example):
    QueryRequest.model_validate(example)


@pytest.mark.parametrize("name", CONTRACT["responses"])
def test_every_response_in_the_contract_round_trips(name):
    example = CONTRACT["responses"][name]
    assert QueryResponse.model_validate(example).model_dump(by_alias=True, mode="json") == example   # as sent


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
    app.state.query_flow = FakeService(error=error)
    r = client.post("/query", json=body(), headers=HEADERS)
    assert r.status_code == status
    assert list(r.json()) == ["error"]
    assert "cites [9]" not in r.text
    assert "bug" not in r.text


def test_every_slot_being_taken_is_a_503_that_says_when_to_come_back(client):
    app.state.query_flow = FakeService(error=ModelBusy("every model call slot is taken"))
    r = client.post("/query", json=body(), headers=HEADERS)
    assert r.status_code == 503
    assert r.headers["retry-after"] == "1"
    assert list(r.json()) == ["error"]


def test_a_rate_limited_provider_passes_on_when_to_come_back(client):
    response = httpx.Response(429, headers={"retry-after": "20"}, request=REQUEST)
    app.state.query_flow = FakeService(error=openai.RateLimitError("slow down", response=response, body=None))
    r = client.post("/query", json=body(), headers=HEADERS)
    assert r.status_code == 503
    assert r.headers["retry-after"] == "20"


def test_a_rate_limited_provider_that_gives_no_time_still_gets_an_answer_with_one(client):
    app.state.query_flow = FakeService(error=openai.RateLimitError("slow down", response=httpx.Response(429, request=REQUEST), body=None))
    assert client.post("/query", json=body(), headers=HEADERS).headers["retry-after"] == "1"


def test_other_failures_invent_no_time_to_come_back(client):
    app.state.query_flow = FakeService(error=psycopg.OperationalError("down"))
    assert "retry-after" not in client.post("/query", json=body(), headers=HEADERS).headers


def test_the_pools_timeout_counts_as_the_search_being_unavailable(client):
    from psycopg_pool import PoolTimeout
    app.state.query_flow = FakeService(error=PoolTimeout("no connection"))
    assert client.post("/query", json=body(), headers=HEADERS).status_code == 503


def test_a_query_is_stopped_when_its_time_is_up_even_if_no_layer_checks(client, monkeypatch):
    monkeypatch.setattr(settings, "query_budget_seconds", 0.05)
    app.state.query_flow = FakeService(delay=5)
    r = client.post("/query", json=body(), headers=HEADERS)
    assert r.status_code == 504
    assert "0.05" in r.json()["error"]


def test_the_service_gets_a_budget_of_the_configured_length(client):
    client.post("/query", json=body(), headers=HEADERS)
    remaining = app.state.query_flow.calls[0]["budget"].remaining()
    assert 0 < remaining <= settings.query_budget_seconds


# ---- every question it takes in is recorded


def test_an_answer_is_recorded_under_the_query_id_it_goes_back_with(client):
    session = "5f0c1c9e-3c4e-4a3e-9d4e-2f1b7a6c8d90"
    app.state.query_flow = FakeService(delay=0.05)
    r = client.post("/query", json=body(sessionId=session), headers=HEADERS)
    [record] = app.state.audit_log.records
    assert r.json()["queryId"] == str(record.query_id) == str(QUERY_ID)
    assert app.state.query_flow.calls[0]["query_id"] == str(QUERY_ID)   # the log line carries it too
    assert (record.user_id, record.role, record.department, str(record.session_id), record.question) == (
        2, "staff", "Beauty", session, "How do I handle a restriction?")
    assert record.outcome == ANSWERED and record.trace.answer.text == "Do this [1]."
    assert record.total_ms >= 50   # timed from when the request arrived


@pytest.mark.parametrize("answer", [from_documents(REFUSED, REFUSAL_TEXT, []),
                                    FlowAnswer(FAILED, FAILED_TEXT, [], ["Beauty"], ("data",), None, None),
                                    FlowAnswer("clarify", "Which one?", [], None, (), None, None)])
def test_a_refusal_a_failed_query_and_a_question_asked_back_are_recorded_with_their_status(client, answer):
    app.state.query_flow = FakeService(answer)
    assert client.post("/query", json=body(), headers=HEADERS).status_code == 200
    assert [r.outcome for r in app.state.audit_log.records] == [answer.status]


@pytest.mark.parametrize(("error", "outcome"), [
    (TimeoutError("budget"), audit.TIMEOUT),
    (openai.APITimeoutError(REQUEST), audit.TIMEOUT),
    (InvalidAnswer("cites [9]"), audit.INVALID),
    (ModelBusy("every model call slot is taken"), audit.BUSY),
    (openai.RateLimitError("slow down", response=httpx.Response(429, request=REQUEST), body=None), audit.RATE_LIMITED),
    (openai.APIConnectionError(request=REQUEST), audit.MODEL_ERROR),
    (openai.APIStatusError("bad", response=httpx.Response(500, request=REQUEST), body=None), audit.MODEL_ERROR),
    (psycopg.OperationalError("down"), audit.DATABASE_ERROR),
    (RuntimeError("bug"), audit.ERROR),
])
def test_a_question_that_brought_no_answer_back_is_recorded_with_what_became_of_it(client, error, outcome):
    app.state.query_flow = FakeService(error=error)
    client.post("/query", json=body(), headers=HEADERS)
    [record] = app.state.audit_log.records
    assert (record.outcome, record.trace, record.question) == (outcome, None, "How do I handle a restriction?")


def test_an_answer_that_failed_validation_is_a_502_recorded_with_its_trace(client):
    app.state.query_flow = FakeService(invalid=InvalidAnswer("cites [9]"))
    r = client.post("/query", json=body(sessionId="5f0c1c9e-3c4e-4a3e-9d4e-2f1b7a6c8d90"), headers=HEADERS)
    assert r.status_code == 502 and "cites [9]" not in r.text
    [record] = app.state.audit_log.records
    assert record.outcome == audit.INVALID and isinstance(record.trace.error, InvalidAnswer)
    assert app.state.query_flow.remembered == []


def test_a_question_past_its_time_is_recorded_after_the_budget_not_cut_off_with_it(client, monkeypatch):
    monkeypatch.setattr(settings, "query_budget_seconds", 0.05)
    app.state.query_flow = FakeService(delay=5)
    assert client.post("/query", json=body(), headers=HEADERS).status_code == 504
    assert [r.outcome for r in app.state.audit_log.records] == [audit.TIMEOUT]


def test_an_answer_that_cant_be_recorded_is_withheld_and_its_turn_not_kept(client):
    app.state.audit_log = FakeAudit(error=psycopg.OperationalError("disk full"))
    r = client.post("/query", json=body(sessionId="5f0c1c9e-3c4e-4a3e-9d4e-2f1b7a6c8d90"), headers=HEADERS)
    assert r.status_code == 503 and r.headers["retry-after"] == "1"
    assert r.json() == {"error": "the question couldn't be recorded; try again shortly"}
    assert "Do this" not in r.text and app.state.query_flow.remembered == []


def test_the_pools_timeout_withholds_the_answer_too(client):
    from psycopg_pool import PoolTimeout
    app.state.audit_log = FakeAudit(error=PoolTimeout("no connection"))
    assert client.post("/query", json=body(), headers=HEADERS).status_code == 503


def test_a_failure_that_cant_be_recorded_still_goes_back_as_the_failure_it_was(client):
    app.state.query_flow = FakeService(error=openai.APIConnectionError(request=REQUEST))
    app.state.audit_log = FakeAudit(error=psycopg.OperationalError("disk full"))
    r = client.post("/query", json=body(), headers=HEADERS)
    assert r.status_code == 502 and r.json() == {"error": "the model call failed"}


def test_a_conversations_turn_is_kept_only_after_the_question_is_recorded(client):
    events = []
    app.state.query_flow = FakeService(events=events)
    app.state.audit_log = FakeAudit(events=events)
    session = "5f0c1c9e-3c4e-4a3e-9d4e-2f1b7a6c8d90"
    client.post("/query", json=body(sessionId=session), headers=HEADERS)
    assert events == ["write", "remember"]
    assert app.state.query_flow.remembered == [(2, session, "How do I handle a restriction?", "Do this [1].")]


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


def start_the_entry_point(monkeypatch, *arguments):
    seen = {}
    previous = asyncio.get_event_loop_policy()
    monkeypatch.setattr(server.uvicorn, "run", lambda target, **kwargs: seen.update(kwargs))
    monkeypatch.setattr(sys, "argv", ["app.server", *arguments])
    try:
        server.main()
    finally:
        asyncio.set_event_loop_policy(previous)
    return seen


def test_the_number_of_processes_comes_from_the_setting(monkeypatch):
    monkeypatch.setattr(settings, "llm_service_workers", 4)
    assert start_the_entry_point(monkeypatch)["workers"] == 4


def test_the_command_line_can_choose_another_number_of_processes(monkeypatch):
    monkeypatch.setattr(settings, "llm_service_workers", 4)
    assert start_the_entry_point(monkeypatch, "--workers", "2")["workers"] == 2


def test_logging_is_configured_through_uvicorn_so_that_every_worker_logs(monkeypatch):
    log_config = start_the_entry_point(monkeypatch, "--workers", "2")["log_config"]
    assert log_config["root"]["level"] == "INFO"
    assert log_config["root"]["handlers"]
    assert not log_config["disable_existing_loggers"]   # the loggers of app.* exist by the time it is applied


def test_the_service_keeps_an_idle_connection_longer_than_the_backends_client_does(monkeypatch):
    # Whoever closes first must be the side that sends the next request, or that request can land on a closed connection
    assert start_the_entry_point(monkeypatch)["timeout_keep_alive"] > 30


def test_development_starts_one_process_by_default():
    assert type(settings).model_fields["llm_service_workers"].default == 1


def test_the_default_limit_on_model_calls_is_one_process_share_of_the_peak_not_all_of_it():
    # At 900 per process, 4 processes would allow 3,600 and the limit would protect nothing
    assert 225 <= type(settings).model_fields["model_max_concurrent_calls"].default < 900


def test_the_pools_of_8_processes_leave_room_for_the_backend_in_the_connections_postgres_allows():
    fields = type(settings).model_fields
    per_process = fields["query_pool_size"].default + fields["sql_pool_size"].default + fields["audit_pool_size"].default
    assert 8 * per_process + 10 < 100


def test_the_service_listens_on_localhost_unless_told_otherwise():
    assert type(settings).model_fields["llm_service_host"].default == "127.0.0.1"


@pytest.mark.anyio
async def test_it_refuses_to_start_without_the_internal_token(monkeypatch):
    monkeypatch.setattr(settings, "internal_service_token", "")
    with pytest.raises(RuntimeError, match="INTERNAL_SERVICE_TOKEN"):
        async with lifespan(app):
            pass


@pytest.mark.anyio
async def test_the_started_service_has_one_model_limit_for_every_call_sized_from_the_settings(monkeypatch):
    class FakePool:
        def __init__(self, *args, **kwargs):
            pass

        async def open(self):
            pass

        async def close(self):
            pass
    monkeypatch.setattr(settings, "internal_service_token", TOKEN)
    monkeypatch.setattr(settings, "openai_api_key", "test-key")
    monkeypatch.setattr(settings, "model_max_concurrent_calls", 7)
    monkeypatch.setattr(settings, "model_slot_wait_seconds", 0.25)
    monkeypatch.setattr(settings, "compose_reasoning_effort", "low")
    monkeypatch.setattr(db, "query_pool", FakePool)
    monkeypatch.setattr(db, "sql_pool", FakePool)
    monkeypatch.setattr(db, "audit_pool", FakePool)
    monkeypatch.setattr(settings, "audit_wait_seconds", 0.3)
    async with lifespan(app):
        assert isinstance(app.state.audit_log._pool, FakePool) and app.state.audit_log._wait == 0.3
        flow = app.state.query_flow
        limits = {id(model._limit) for model in (flow._embedder, flow._router._model, flow._documents._model,
                                                  flow._data._writer, flow._data._explainer, flow._composer)}
        limit = flow._embedder._limit
        assert len(limits) == 1
        assert limit._semaphore._value == 7
        assert limit._max_wait == 0.25
        assert flow._router._min_confidence == settings.router_min_confidence
        assert (flow._composer._reasoning_effort, flow._documents._model._reasoning_effort) == ("low", "none")


@pytest.mark.anyio
async def test_it_refuses_to_start_without_the_sql_readers_password(monkeypatch):
    monkeypatch.setattr(settings, "internal_service_token", TOKEN)
    monkeypatch.setattr(settings, "openai_api_key", "test-key")
    monkeypatch.setattr(settings, "sql_reader_password", "")
    with pytest.raises(RuntimeError, match="SQL_READER_PASSWORD"):
        async with lifespan(app):
            pass


def test_the_contract_has_the_cases_it_should():
    assert set(CONTRACT["responses"]) == {"answered", "refused", "answeredFromData", "answeredFromBoth",
                                          "answeredAboutTheLastCampaign", "clarify", "noSuchCampaign", "failed"}
    with pytest.raises(ValidationError):
        QueryRequest.model_validate({**CONTRACT["requests"][0], "role": "intern"})
