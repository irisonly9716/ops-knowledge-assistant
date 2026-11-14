"""Answers cached in Redis, so a question asked again is answered without a model call.

    cache:<department>:<role>:<versions>:<sha256 of the question>

- The department and the role, because what an answer may say depends on them: without them one department's answer
  would reach another. Within a department and a role everyone sees the same, so no user id.
- The versions (migration V12): a staff member's or a manager's department and 'all', an executive's or admin's
  'everything'. Every write of a document or a table moves them on in its own transaction, so an answer cached before
  it is never found again. Nothing is deleted: Redis lets the old keys expire after cache_ttl_seconds. Reading them is
  one more query a question, about a millisecond.
- The question, compared the way the rewrite compares (rewriting.normalized): case, spacing and the closing mark
  don't make another question. Not a semantic cache: "last month's GMV" and "GMV the month before" are close in meaning
  and have different answers.

Which question is the key is the query flow's choice (app/query_flow.py): the question as asked only when the rewrite
judged it to stand on its own, and otherwise the question rewritten with its campaign and dates filled in, so "the
last Black Friday" is cached under the one it meant and a newer one finishing misses it.

The TTL is a backstop for what no write moves on: a campaign running yesterday has finished today.

Cached: answers and refusals. Not cached: a question asked back, which waits for the asker's reply; "failed", which
may succeed next time; errors.
"""
import asyncio
import hashlib
import json
import logging
from dataclasses import asdict
from datetime import date
from typing import Any

import psycopg
from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.query_flow import FlowAnswer
from app.rag.answering import ANSWERED, REFUSED, Citation
from app.rag.retrieval import ROLES_THAT_SEE_EVERYTHING, RetrievedChunk
from app.router.rewriting import normalized
from app.sql_agent.campaigns import Campaign
from app.sql_agent.execute import QueryResult
from app.time_budget import TimeBudget

log = logging.getLogger("query")

CACHED_STATUSES = (ANSWERED, REFUSED)
_VERSIONS = """
    SELECT kv.scope, kv.version FROM knowledge_versions kv LEFT JOIN departments d USING (department_id)
    WHERE kv.scope = 'all' OR (kv.scope = 'department' AND d.name = %s)
"""
_EVERYTHING = "SELECT version FROM knowledge_versions WHERE scope = 'everything'"


def key(department: str | None, role: str, versions: str, question: str) -> str:
    digest = hashlib.sha256(normalized(question).encode()).hexdigest()
    return f"cache:{department or '*'}:{role}:{versions}:{digest}"


def serialized(answer: FlowAnswer) -> str:
    """The excerpts' text isn't kept: the response shows where a citation is from, not what it says."""
    data = asdict(answer)
    for citation in data["citations"]:
        citation["chunk"]["content"] = ""
    return json.dumps(data, default=lambda value: value.isoformat() if isinstance(value, date) else str(value))


def deserialized(text: str) -> FlowAnswer:
    data: dict[str, Any] = json.loads(text)
    result = data["result"]
    return FlowAnswer(
        data["status"], data["text"],
        [Citation(c["number"], RetrievedChunk(**{**c["chunk"], "heading_path": tuple(c["chunk"]["heading_path"])}))
         for c in data["citations"]],
        data["searched_departments"], tuple(data["paths"]), data["sql"],
        QueryResult(tuple(result["columns"]), tuple(tuple(row) for row in result["rows"]), result["truncated"])
        if result else None,
        _campaign(data["basis"]) if data["basis"] else None, tuple(_campaign(c) for c in data["candidates"]))


def _campaign(data: dict) -> Campaign:
    return Campaign(data["campaign_id"], data["name"], data["department"], date.fromisoformat(data["start_date"]),
                    date.fromisoformat(data["end_date"]))


class AnswerCache:
    def __init__(self, redis: Redis, pool: Any, *, ttl_seconds: int, timeout_seconds: float = 0.1):
        self._redis = redis
        self._pool = pool      # the query path's, as ops: knowledge_versions
        self._ttl = ttl_seconds
        self._timeout = timeout_seconds   # the hard stop of every Redis call (redis_client.py)

    @staticmethod
    def key(department: str | None, role: str, versions: str, question: str) -> str:
        return key(department, role, versions, question)

    async def versions(self, role: str, department: str | None, budget: TimeBudget) -> str | None:
        """The versions part of the key; None when the database can't say, and then nothing is cached or found."""
        try:
            async with self._pool.connection(timeout=max(budget.remaining(), 0)) as conn:
                if role in ROLES_THAT_SEE_EVERYTHING:
                    row = await (await conn.execute(_EVERYTHING)).fetchone()
                    return f"e{row[0] if row else 0}"
                found = dict(await (await conn.execute(_VERSIONS, (department,))).fetchall())
        except psycopg.Error as error:
            log.warning("knowledge versions unavailable, the cache is skipped: %s", type(error).__name__)
            return None
        return f"d{found.get('department', 0)}.a{found.get('all', 0)}"

    async def get(self, cache_key: str) -> FlowAnswer | None:
        try:
            async with asyncio.timeout(self._timeout):
                text = await self._redis.get(cache_key)
        except (RedisError, TimeoutError) as error:
            log.warning("answer cache unavailable: %s", type(error).__name__)
            return None
        return deserialized(text) if text else None

    async def put(self, cache_key: str, answer: FlowAnswer) -> bool:
        """Whether it was kept: only answers and refusals are."""
        if answer.status not in CACHED_STATUSES:
            return False
        try:
            async with asyncio.timeout(self._timeout):
                await self._redis.set(cache_key, serialized(answer), ex=self._ttl)
        except (RedisError, TimeoutError) as error:
            log.warning("answer cache unavailable, the answer isn't kept: %s", type(error).__name__)
            return False
        return True
