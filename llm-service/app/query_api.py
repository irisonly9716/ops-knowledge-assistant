"""POST /query: the backend's way into the query path. Never exposed to end users.

Whoever can reach this endpoint can claim to be an executive and read everything, because the role and department
come in the request: the backend looks them up from its users table and this service trusts them. So every call
must carry the shared internal token, and the service must not be reachable from outside (docs/permission-model.md).

The time budget is enforced twice on purpose. `asyncio.timeout` is the hard stop: it cancels whatever is running,
so the bound holds even if a layer forgets to check. TimeBudget lets the layers decide before that, so a retry that
can't fit gives up at once and the error says why.

Every question that gets past the token and the request's checks is recorded (app/audit.py), whatever became of it,
before anything goes back. An answer whose row can't be written is withheld: a 503, and the conversation doesn't keep
the turn. A failure goes back as the failure it was even when its row can't be written. The row is written after the
budget, so the worst case is the budget plus the audit's wait for a connection and its insert.
"""
import asyncio
import hmac
import logging
import time
from typing import Literal
from uuid import UUID, uuid4

import psycopg
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from openai import APIError, APITimeoutError, RateLimitError
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic.alias_generators import to_camel

from app import audit
from app.audit import AuditLog, AuditRecord
from app.config import settings
from app.query_flow import FlowAnswer, FlowTrace, QueryFlow
from app.sql_agent.campaigns import Campaign
from app.rag.answering import InvalidAnswer
from app.rag.query_models import ModelBusy
from app.rag.retrieval import ROLES_LIMITED_TO_A_DEPARTMENT
from app.time_budget import TimeBudget

log = logging.getLogger("query")

MAX_QUESTION_CHARS = 2000   # the backend refuses longer questions with the same limit

router = APIRouter()


class _CamelModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid")


class QueryRequest(_CamelModel):
    question: str = Field(min_length=1, max_length=MAX_QUESTION_CHARS)
    user_id: int
    role: Literal["staff", "manager", "executive", "admin"]
    department: str | None
    session_id: UUID | None = None   # the conversation the question belongs to; none: a question on its own

    @field_validator("question")
    @classmethod
    def question_has_content(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("the question is empty")
        return value.strip()

    @model_validator(mode="after")
    def department_fits_role(self) -> "QueryRequest":
        # The same rule as the CHECK constraint on users. A mismatch means a bug in the caller: refuse it
        needs_department = self.role in ROLES_LIMITED_TO_A_DEPARTMENT
        if needs_department != bool(self.department):
            raise ValueError(f"role {self.role} {'needs a department' if needs_department else 'has no department'}")
        return self


class CitationResponse(_CamelModel):
    number: int
    doc_id: int
    doc_type: str
    title: str
    department: str
    heading_path: list[str]
    score: float


class DataResponse(_CamelModel):
    """The query an answer rests on and every row it returned, values as text so numbers stay exact."""
    sql: str
    columns: list[str]
    rows: list[list[str | None]]
    truncated: bool   # the query returned more rows than are kept


class CampaignResponse(_CamelModel):
    department: str
    campaign_name: str
    start_date: str   # ISO dates
    end_date: str

    @classmethod
    def of(cls, campaign: Campaign) -> "CampaignResponse":
        return cls(department=campaign.department, campaign_name=campaign.name,
                   start_date=campaign.start_date.isoformat(), end_date=campaign.end_date.isoformat())


class QueryResponse(_CamelModel):
    query_id: UUID                           # the question's audit row: what the asker's feedback names
    # failed: no query that answers it could be written; clarify: the question is asked back, with candidates
    status: Literal["answered", "refused", "failed", "clarify"]
    answer: str
    citations: list[CitationResponse]
    searched_departments: list[str] | None   # null: all departments
    paths: list[Literal["documents", "data"]]
    data: DataResponse | None
    basis: CampaignResponse | None           # the campaign "the last one" turned out to be
    candidates: list[CampaignResponse] | None   # only when asked back

    @classmethod
    def of(cls, result: FlowAnswer, query_id: UUID) -> "QueryResponse":
        rows = result.result
        data = (DataResponse(sql=result.sql, columns=list(rows.columns), rows=[list(row) for row in rows.rows],
                             truncated=rows.truncated) if rows is not None else None)
        return cls(query_id=query_id, status=result.status, answer=result.text, searched_departments=result.searched_departments,
                   citations=[CitationResponse(number=c.number, doc_id=c.chunk.doc_id, doc_type=c.chunk.doc_type,
                                               title=c.chunk.title, department=c.chunk.department,
                                               heading_path=list(c.chunk.heading_path), score=round(c.chunk.score, 4))
                              for c in result.citations],
                   paths=list(result.paths), data=data,
                   basis=CampaignResponse.of(result.basis) if result.basis else None,
                   candidates=[CampaignResponse.of(c) for c in result.candidates] or None)


def require_backend(x_internal_token: str | None = Header(default=None)) -> None:
    expected = settings.internal_service_token
    # compare_digest takes the same time wherever the strings differ, so the token can't be guessed byte by byte
    if not expected or not x_internal_token or not hmac.compare_digest(x_internal_token.encode(), expected.encode()):
        raise HTTPException(401, "this endpoint only accepts calls from the backend")


def query_flow(request: Request) -> QueryFlow:
    return request.app.state.query_flow


def audit_log(request: Request) -> AuditLog:
    return request.app.state.audit_log


class _Failed(Exception):
    """A question that brought no answer back: what the audit records, and the error the backend gets."""
    def __init__(self, outcome: str, error: HTTPException, trace: FlowTrace | None = None):
        super().__init__(outcome)
        self.outcome, self.error, self.trace = outcome, error, trace


@router.post("/query", dependencies=[Depends(require_backend)], response_model=QueryResponse, response_model_by_alias=True)
async def query(body: QueryRequest, flow: QueryFlow = Depends(query_flow),
                audits: AuditLog = Depends(audit_log)) -> QueryResponse:
    query_id, started = uuid4(), time.perf_counter()
    session_id = str(body.session_id) if body.session_id else None

    def record(outcome: str, trace: FlowTrace | None = None) -> AuditRecord:
        return AuditRecord(query_id, body.user_id, body.role, body.department, body.session_id, body.question, outcome,
                           (time.perf_counter() - started) * 1000, trace)

    try:
        trace = await _traced(flow, body, query_id, session_id)
    except _Failed as failed:
        await _recorded_anyway(audits, record(failed.outcome, failed.trace))
        raise failed.error from failed.__cause__
    except Exception:
        await _recorded_anyway(audits, record(audit.ERROR))
        raise
    try:
        await audits.write(record(trace.answer.status, trace))
    except psycopg.Error as e:
        log.error("query id=%s user=%s not recorded, so its answer is withheld: %s", query_id, body.user_id,
                  type(e).__name__)
        raise HTTPException(503, "the question couldn't be recorded; try again shortly",
                            headers={"Retry-After": "1"}) from e
    await flow.remember(body.user_id, session_id, body.question, trace.answer)
    return QueryResponse.of(trace.answer, query_id)


async def _traced(flow: QueryFlow, body: QueryRequest, query_id: UUID, session_id: str | None) -> FlowTrace:
    """The flow's trace of an answer, or _Failed with the outcome to record and the error to send."""
    budget_seconds = settings.query_budget_seconds
    try:
        async with asyncio.timeout(budget_seconds):
            trace = await flow.answer(body.question, session_id=session_id, user_id=body.user_id, role=body.role,
                                      department=body.department, budget=TimeBudget(budget_seconds),
                                      query_id=str(query_id))
    except (TimeoutError, APITimeoutError) as e:
        log.warning("query id=%s user=%s ran out of time: %s", query_id, body.user_id, type(e).__name__)
        raise _Failed(audit.TIMEOUT, HTTPException(504, f"no answer within {budget_seconds:g} s")) from e
    except InvalidAnswer as e:
        log.warning("query id=%s user=%s answer refused: %s", query_id, body.user_id, e)
        raise _Failed(audit.INVALID, HTTPException(502, "the model's answer failed validation")) from e
    except ModelBusy as e:
        log.warning("query id=%s user=%s refused: every model call slot is taken", query_id, body.user_id)
        raise _Failed(audit.BUSY, HTTPException(503, "too many questions are being answered right now; try again shortly",
                                                headers={"Retry-After": "1"})) from e
    except RateLimitError as e:
        log.warning("query id=%s user=%s rate limited by the model provider", query_id, body.user_id)
        raise _Failed(audit.RATE_LIMITED, HTTPException(
            503, "the model provider is rate limiting; try again shortly",
            headers={"Retry-After": e.response.headers.get("retry-after", "1")})) from e
    except APIError as e:
        log.warning("query id=%s user=%s model call failed: %s", query_id, body.user_id, type(e).__name__)
        raise _Failed(audit.MODEL_ERROR, HTTPException(502, "the model call failed")) from e
    except psycopg.Error as e:   # includes the pool's timeout
        log.warning("query id=%s user=%s database error: %s", query_id, body.user_id, type(e).__name__)
        raise _Failed(audit.DATABASE_ERROR, HTTPException(503, "the documents and data are unavailable")) from e
    if trace.error:
        log.warning("query id=%s user=%s answer refused: %s", query_id, body.user_id, trace.error)
        raise _Failed(audit.INVALID, HTTPException(502, "the model's answer failed validation"), trace) from trace.error
    return trace


async def _recorded_anyway(audits: AuditLog, record: AuditRecord) -> None:
    """A failure's row. If it can't be written either, the failure still goes back as it was, and the log says so."""
    try:
        await audits.write(record)
    except psycopg.Error as e:
        log.error("query id=%s user=%s not recorded: %s", record.query_id, record.user_id, type(e).__name__)
