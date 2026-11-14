"""The turns of a conversation, kept in Redis so a follow-up can be rewritten into a question that stands on its own.

Only the rewrite and the router read them (app/router/rewriting.py): an answer comes from the documents and the data,
never from the conversation, so the turns only have to say what "it" or "the one before that" means. The last
memory_messages are kept (10 questions and their answers), a list per conversation:

    RPUSH the question and the answer -> LTRIM to the last memory_messages -> EXPIRE memory_ttl_seconds

in one transaction, so a reader never sees a question without its answer or a list past its length. The rewrite gets
the last three questions and answers in full and the questions before them alone (rewriting.conversation): a
question is a few words, an answer up to memory_answer_chars, so the older topics cost the rewrite little.

The key holds the user's id, which the backend takes from its users table, never from the request: a conversation id
someone else's client made up reads nothing of theirs.

A conversation expires memory_ttl_seconds after its last turn. Redis down or slow: no turns, and nothing kept
(redis_client.py); the question is answered as if it were the first.
"""
import asyncio
import json
import logging

from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.router.rewriting import Turn

log = logging.getLogger("query")


def key(user_id: int, session_id: str) -> str:
    return f"memory:{user_id}:{session_id}"


class ConversationMemory:
    def __init__(self, redis: Redis, *, messages: int, ttl_seconds: int, answer_chars: int,
                 timeout_seconds: float = 0.1):
        self._redis = redis
        self._timeout = timeout_seconds   # the hard stop of every Redis call (redis_client.py)
        self._messages = messages
        self._ttl = ttl_seconds
        self._answer_chars = answer_chars

    async def recent(self, user_id: int, session_id: str) -> tuple[Turn, ...]:
        """The kept turns, oldest first; none when Redis can't say."""
        try:
            async with asyncio.timeout(self._timeout):
                stored = await self._redis.lrange(key(user_id, session_id), 0, -1)
        except (RedisError, TimeoutError) as error:
            log.warning("conversation memory unavailable, answering without it: %s", type(error).__name__)
            return ()
        turns = []
        for item in stored:
            message = json.loads(item)
            turns.append(Turn(message["role"], message["content"]))
        return tuple(turns)

    async def remember(self, user_id: int, session_id: str, question: str, answer: str) -> None:
        """The question as asked and the answer as shown, cut to answer_chars: the rows and the query aren't kept."""
        name = key(user_id, session_id)
        messages = [json.dumps({"role": "user", "content": question}),
                    json.dumps({"role": "assistant", "content": answer[:self._answer_chars]})]
        try:
            async with asyncio.timeout(self._timeout), self._redis.pipeline(transaction=True) as pipe:
                pipe.rpush(name, *messages)
                pipe.ltrim(name, -self._messages, -1)
                pipe.expire(name, self._ttl)
                await pipe.execute()
        except (RedisError, TimeoutError) as error:
            log.warning("conversation memory unavailable, the turn isn't kept: %s", type(error).__name__)
