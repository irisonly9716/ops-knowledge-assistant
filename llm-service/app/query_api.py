"""POST /query: the backend's way into the query path. Never exposed to end users.

Whoever can reach this endpoint can claim to be an executive and read everything, because the role and department
come in the request: the backend looks them up from its users table and this service trusts them. So every call
must carry the shared internal token, and the service must not be reachable from outside (docs/permission-model.md).

The time budget is enforced twice on purpose. `asyncio.timeout` is the hard stop: it cancels whatever is running,
so the bound holds even if a layer forgets to check. TimeBudget lets the layers decide before that, so a retry that
can't fit gives up at once and the error says why.
"""
import asyncio
import hmac
import logging
from typing import Literal

import psycopg
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from openai import APIError, APITimeoutError, RateLimitError
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic.alias_generators import to_camel

from app.config import settings
from app.rag.answering import InvalidAnswer, QueryAnswer, QueryService
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


class QueryResponse(_CamelModel):
    status: Literal["answered", "refused"]
    answer: str
    citations: list[CitationResponse]
    searched_departments: list[str] | None   # null: all departments

    @classmethod
    def of(cls, result: QueryAnswer) -> "QueryResponse":
        return cls(status=result.status, answer=result.text, searched_departments=result.searched_departments,
                   citations=[CitationResponse(number=c.number, doc_id=c.chunk.doc_id, doc_type=c.chunk.doc_type,
                                               title=c.chunk.title, department=c.chunk.department,
                                               heading_path=list(c.chunk.heading_path), score=round(c.chunk.score, 4))
                              for c in result.citations])


def require_backend(x_internal_token: str | None = Header(default=None)) -> None:
    expected = settings.internal_service_token
    # compare_digest takes the same time wherever the strings differ, so the token can't be guessed byte by byte
    if not expected or not x_internal_token or not hmac.compare_digest(x_internal_token.encode(), expected.encode()):
        raise HTTPException(401, "this endpoint only accepts calls from the backend")


def query_service(request: Request) -> QueryService:
    return request.app.state.query_service


@router.post("/query", dependencies=[Depends(require_backend)], response_model=QueryResponse, response_model_by_alias=True)
async def query(body: QueryRequest, service: QueryService = Depends(query_service)) -> QueryResponse:
    budget_seconds = settings.query_budget_seconds
    try:
        async with asyncio.timeout(budget_seconds):
            result = await service.answer(body.question, user_id=body.user_id, role=body.role,
                                          department=body.department, budget=TimeBudget(budget_seconds))
    except (TimeoutError, APITimeoutError) as e:
        log.warning("query user=%s ran out of time: %s", body.user_id, type(e).__name__)
        raise HTTPException(504, f"no answer within {budget_seconds:g} s") from e
    except InvalidAnswer as e:
        log.warning("query user=%s answer refused: %s", body.user_id, e)
        raise HTTPException(502, "the model's answer failed validation") from e
    except RateLimitError as e:
        log.warning("query user=%s rate limited by the model provider", body.user_id)
        raise HTTPException(503, "the model provider is rate limiting; try again shortly") from e
    except APIError as e:
        log.warning("query user=%s model call failed: %s", body.user_id, type(e).__name__)
        raise HTTPException(502, "the model call failed") from e
    except psycopg.Error as e:   # includes the pool's timeout
        log.warning("query user=%s database error: %s", body.user_id, type(e).__name__)
        raise HTTPException(503, "the document search is unavailable") from e
    return QueryResponse.of(result)
