"""The answer cache and the conversation memory: their keys, what they keep and for how long, against the compose Redis
(database 15) and a throwaway Postgres; and that a Redis that is down only means no cache and no conversation."""
import asyncio
import time
from datetime import date

import pytest
from postgres_helpers import postgres_available, throwaway_database
from redis_helpers import needs_redis, test_redis, unreachable_redis  # noqa: F401  (test_redis is a fixture)

from app import db
from app.memory import cache as answer_cache
from app.memory.cache import AnswerCache
from app.memory.conversation import ConversationMemory
from app.query_flow import CLARIFY, FlowAnswer
from app.rag.answering import ANSWERED, REFUSED, Citation
from app.rag.retrieval import RetrievedChunk
from app.rag.store import bump_knowledge_versions
from app.router.rewriting import Turn
from app.sql_agent.campaigns import Campaign
from app.sql_agent.execute import QueryResult
from app.sql_agent.pipeline import FAILED
from app.time_budget import TimeBudget

needs_postgres = pytest.mark.skipif(not postgres_available(), reason="needs the docker compose Postgres")
CHUNK = RetrievedChunk(chunk_id=7, doc_id=41, doc_type="campaign_plan", title="Beauty Black Friday Live - Campaign plan",
                       department="Beauty", heading_path=("Plan", "Targets"), content="Target: 8% conversion.",
                       score=0.6127)
CAMPAIGN = Campaign(3, "Beauty Black Friday Live", "Beauty", date(2025, 11, 18), date(2025, 12, 14))
ANSWER = FlowAnswer(ANSWERED, "Based on ...\n\nIt converted 8.6% [1].", [Citation(1, CHUNK)], ["Beauty", "ALL"],
                    ("documents", "data"), "SELECT 1",
                    QueryResult(("campaign_name", "avg_conversion_rate"), (("Beauty Black Friday Live", "0.086"),
                                                                           ("Beauty Fan Day", None)), False),
                    CAMPAIGN, ())


# ---- the cache's key and what it keeps


def test_the_key_holds_department_role_versions_and_the_question_as_the_rewrite_compares_it():
    staff = answer_cache.key("Beauty", "staff", "d12.a3", "What was the GMV?")
    assert staff.startswith("cache:Beauty:staff:d12.a3:")
    assert staff == answer_cache.key("Beauty", "staff", "d12.a3", "  what was the  GMV ")   # case, spacing, the mark
    assert staff != answer_cache.key("Fresh Grocery", "staff", "d12.a3", "What was the GMV?")
    assert staff != answer_cache.key("Beauty", "manager", "d12.a3", "What was the GMV?")
    assert staff != answer_cache.key("Beauty", "staff", "d13.a3", "What was the GMV?")
    assert answer_cache.key(None, "executive", "e40", "What was the GMV?").startswith("cache:*:executive:e40:")


def test_an_answer_comes_back_from_the_cache_as_it_went_in_without_the_excerpts_text():
    back = answer_cache.deserialized(answer_cache.serialized(ANSWER))
    assert back.citations[0].chunk.content == ""   # where a citation is from is shown, not what it says
    assert back.citations[0].chunk.heading_path == ("Plan", "Targets")
    assert back == FlowAnswer(**{**ANSWER.__dict__, "citations": [Citation(1, RetrievedChunk(
        **{**CHUNK.__dict__, "content": ""}))]})
    refusal = FlowAnswer(REFUSED, "I couldn't find this.", [], None, ("documents",), None, None)
    assert answer_cache.deserialized(answer_cache.serialized(refusal)) == refusal


@needs_redis
@pytest.mark.anyio
async def test_answers_and_refusals_are_kept_with_an_expiry_and_nothing_else_is(test_redis):
    cache = AnswerCache(test_redis, None, ttl_seconds=60)
    assert await cache.put("cache:k1", ANSWER) and await cache.get("cache:k1") is not None
    assert 0 < await test_redis.ttl("cache:k1") <= 60
    asked_back = FlowAnswer(CLARIFY, "Which one?", [], None, (), None, None, None, (CAMPAIGN,))
    failed = FlowAnswer(FAILED, "I couldn't write a query.", [], ["Beauty"], ("data",), None, None)
    assert not await cache.put("cache:k2", asked_back) and not await cache.put("cache:k3", failed)
    assert await test_redis.keys("*") == ["cache:k1"]
    assert await cache.get("cache:missing") is None


@pytest.mark.anyio
async def test_a_redis_that_is_down_is_a_miss_and_keeps_nothing_quickly():
    down = unreachable_redis()
    cache = AnswerCache(down, None, ttl_seconds=60)
    memory = ConversationMemory(down, messages=20, ttl_seconds=60, answer_chars=1000)
    started = time.perf_counter()
    assert await cache.get("cache:k") is None and not await cache.put("cache:k", ANSWER)
    assert await memory.recent(3, "s") == ()
    await memory.remember(3, "s", "Who signs off?", "The manager.")   # no error
    assert time.perf_counter() - started < 1   # four calls; redis-py's own retries took about 5 s each
    await down.aclose()


# ---- the versions, read from Postgres


@pytest.fixture(scope="module")
def database():
    if not postgres_available():
        pytest.skip("needs the docker compose Postgres")
    with throwaway_database() as name:
        with db.connect(name) as conn:
            conn.execute("INSERT INTO departments (name) VALUES ('Beauty'), ('Fresh Grocery')")
        yield name


@needs_postgres
@pytest.mark.anyio
async def test_the_versions_part_of_the_key_moves_with_the_writes_each_asker_depends_on(database):
    async with db.query_pool(database) as pool:
        await pool.wait()
        cache = AnswerCache(None, pool, ttl_seconds=60)
        versions = lambda role, department: cache.versions(role, department, TimeBudget(10))  # noqa: E731
        beauty, grocery, executive = (await versions("staff", "Beauty"), await versions("staff", "Fresh Grocery"),
                                      await versions("executive", None))
        assert (beauty, executive) == ("d0.a0", "e0")   # nothing written yet
        with db.connect(database) as conn:
            bump_knowledge_versions(conn, "Beauty")
        assert await versions("staff", "Beauty") != beauty and await versions("manager", "Fresh Grocery") == grocery
        assert await versions("admin", None) != executive
        with db.connect(database) as conn:
            bump_knowledge_versions(conn, "ALL")
        assert await versions("staff", "Fresh Grocery") != grocery   # a document for everyone reaches every department


@pytest.mark.anyio
async def test_when_the_versions_cant_be_read_nothing_is_cached():
    class DownPool:
        def connection(self, timeout):
            raise __import__("psycopg").OperationalError("connection refused")
    assert await AnswerCache(None, DownPool(), ttl_seconds=60).versions("staff", "Beauty", TimeBudget(10)) is None


# ---- the conversation


@needs_redis
@pytest.mark.anyio
async def test_a_conversation_keeps_its_last_messages_in_order_with_an_expiry(test_redis):
    memory = ConversationMemory(test_redis, messages=4, ttl_seconds=60, answer_chars=10)
    for n in range(3):
        await memory.remember(3, "s1", f"question {n}", f"answer {n} and more words")
    assert await memory.recent(3, "s1") == (Turn("user", "question 1"), Turn("assistant", "answer 1 a"),
                                            Turn("user", "question 2"), Turn("assistant", "answer 2 a"))
    assert 0 < await test_redis.ttl("memory:3:s1") <= 60


@needs_redis
@pytest.mark.anyio
async def test_a_conversation_belongs_to_its_user_and_its_session(test_redis):
    memory = ConversationMemory(test_redis, messages=20, ttl_seconds=60, answer_chars=1000)
    await memory.remember(3, "s1", "Who owns the inventory check?", "Supply Chain.")
    assert await memory.recent(3, "s2") == ()
    assert await memory.recent(4, "s1") == ()   # another user with the same conversation id reads nothing


@needs_redis
@pytest.mark.anyio
async def test_a_question_and_its_answer_are_kept_together(test_redis):
    memory = ConversationMemory(test_redis, messages=20, ttl_seconds=60, answer_chars=1000)
    await asyncio.gather(*(memory.remember(3, "s", f"q{n}", f"a{n}") for n in range(10)))
    turns = await memory.recent(3, "s")
    assert [t.role for t in turns] == ["user", "assistant"] * 10
    assert all(answer.content == "a" + question.content[1:] for question, answer in zip(turns[::2], turns[1::2]))
