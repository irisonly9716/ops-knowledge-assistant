"""A row in query_audit (migration V13) for every question /query takes in, whatever became of it: who asked, what ran,
what each step took and what its model calls cost.

/query writes it once the question is done (app/query_api.py), answered or failed, before the answer goes back. When
the row can't be written the asker gets no answer: an executive's question about any department must leave a trace,
and a row missing would be invisible later. The wait is bounded: a connection within audit_wait_seconds, the insert
within the connection's statement limit (app/db.py).

The question, its rewrite and its SQL are kept for the retention period, then cleared by the backend; the numbers stay.
They never go to the log, which keeps only the numbers (app/query_flow.py).
"""
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from psycopg.types.json import Jsonb

from app import pricing
from app.config import settings
from app.query_flow import FlowTrace

# What became of a question that brought no answer back; an answer's own status is its outcome otherwise
INVALID, TIMEOUT, BUSY, RATE_LIMITED = "invalid", "timeout", "busy", "rate_limited"
MODEL_ERROR, DATABASE_ERROR, ERROR = "model_error", "database_error", "error"
CACHE_HITS = {"as asked": "as_asked", "rewritten": "rewritten"}


def part_models() -> dict[str, str]:
    """The model each part of the path calls (FlowTrace.parts), for its cost."""
    return {"router": settings.router_model, "rewriter": settings.rewrite_model, "documents": settings.chat_model,
            "writer": settings.sql_model, "explainer": settings.chat_model, "compose": settings.chat_model}


@dataclass(frozen=True)
class AuditRecord:
    query_id: UUID
    user_id: int
    role: str
    department: str | None
    session_id: UUID | None
    question: str
    outcome: str
    total_ms: float                 # as /query measured it, the conversation's turns read before the flow included
    trace: FlowTrace | None = None  # None when nothing came back: past the time limit, or an error on the way

    def row(self) -> dict[str, Any]:
        row = {"query_id": self.query_id, "user_id": self.user_id, "role": self.role, "department": self.department,
               "session_id": self.session_id, "question": self.question, "outcome": self.outcome,
               "total_ms": round(self.total_ms), "cache_hit": None, "route": None, "decided_by": None,
               "confidence": None, "paths": [], "cancelled": [], "written_by": None, "rewritten": None, "sql": None,
               "campaign_id": None, "sql_attempts": None, "result_rows": None, "cited_doc_ids": [], "model_calls": None,
               "input_tokens": None, "output_tokens": None, "tokens": None, "cost_usd": None, "timings_ms": None}
        traced = self.trace
        if traced is None:
            return row
        answer, routing, data = traced.answer, traced.routing, traced.data
        parts = {part: used for part, used in traced.parts().items() if any(used)}
        models = part_models()
        explain_ms = data.explain_ms if data else 0.0
        timings = {"embed": traced.embed_ms, "rewrite": traced.rewrite_ms, "route": traced.route_ms,
                   "paths": traced.paths_ms, "compose": traced.compose_ms, "explain": explain_ms,
                   "flow": traced.total_ms}
        row |= {
            "cache_hit": CACHE_HITS.get(traced.cached),
            "route": routing.route if routing else None,
            "decided_by": routing.decided_by if routing else None,
            "confidence": routing.confidence if routing else None,
            # What the answer rests on: for a cache hit, the paths that ran when it was cached
            "paths": list(answer.paths) if answer else [],
            "cancelled": list(traced.cancelled),
            "written_by": traced.written_by if traced.written_by != "-" else None,
            "rewritten": traced.asked if traced.rewritten and traced.rewritten.changed else None,
            # The query the answer rests on; when none does, the last one written, so a failed question shows what
            # was tried
            "sql": (answer.sql if answer and answer.sql
                    else data.attempts[-1].written if data and data.attempts else None),
            "campaign_id": answer.basis.campaign_id if answer and answer.basis else None,
            "sql_attempts": len(data.attempts) if data else None,
            "result_rows": len(answer.result.rows) if answer and answer.result else None,
            "cited_doc_ids": sorted({c.chunk.doc_id for c in answer.citations}) if answer else [],
            "model_calls": traced.model_calls,
            "input_tokens": traced.tokens[0], "output_tokens": traced.tokens[1],
            "tokens": Jsonb({part: list(used) for part, used in parts.items()}) if parts else None,
            "cost_usd": pricing.cost((models[part], *used) for part, used in parts.items()),
            "timings_ms": Jsonb({step: round(ms) for step, ms in timings.items() if ms}),
        }
        return row


_INSERT = """
    INSERT INTO query_audit (query_id, user_id, role, department, session_id, outcome, cache_hit, route, decided_by,
                             confidence, paths, cancelled, written_by, question, rewritten, sql, campaign_id,
                             sql_attempts, result_rows, cited_doc_ids, model_calls, input_tokens, output_tokens, tokens,
                             cost_usd, total_ms, timings_ms)
    VALUES (%(query_id)s, %(user_id)s, %(role)s, %(department)s, %(session_id)s, %(outcome)s, %(cache_hit)s,
            %(route)s, %(decided_by)s, %(confidence)s, %(paths)s::text[], %(cancelled)s::text[], %(written_by)s,
            %(question)s, %(rewritten)s, %(sql)s, %(campaign_id)s, %(sql_attempts)s, %(result_rows)s,
            %(cited_doc_ids)s::bigint[], %(model_calls)s, %(input_tokens)s, %(output_tokens)s, %(tokens)s,
            %(cost_usd)s, %(total_ms)s, %(timings_ms)s)
"""


class AuditLog:
    def __init__(self, pool: Any, wait_seconds: float):
        """pool: app.db.audit_pool(), opened."""
        self._pool = pool
        self._wait = wait_seconds

    async def write(self, record: AuditRecord) -> None:
        """Raises psycopg.Error when the row isn't written, the pool's timeout among them."""
        async with self._pool.connection(timeout=self._wait) as conn:
            await conn.execute(_INSERT, record.row())
