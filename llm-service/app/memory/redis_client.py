"""The Redis connection of the query path: answers cached and the turns of a conversation (cache.py, conversation.py).

Redis only makes a query faster or lets it understand a follow-up, so it never fails one. Every call is bounded by
redis_timeout_seconds, a wait for a connection included, and an error or a timeout is logged and taken as "nothing
there": the query is answered as if there were no cache and no conversation. A slow Redis can't hold a query up.

Bounded twice, as the query's own budget is (app/query_api.py). A redis-py 8 client built without a pool retries a
failed call 3 times with growing waits: measured with Redis down, one GET took 5.7 s against a 0.1 s socket timeout,
and 0.1 s through a pool, or with retries off. The client here is built on a pool with retries off as well, so neither
change of construction brings a hidden retry back; and each call also runs under asyncio.timeout (cache.py,
conversation.py), which holds whatever the library does.

The pool is bounded too, per process: redis-py's default pool opens a connection for every caller that waits, which
at the peak's 900 queries in flight would be 900 connections a process.
"""
import logging

from redis.asyncio import BlockingConnectionPool, Redis
from redis.asyncio.retry import Retry
from redis.backoff import NoBackoff

from app.config import settings

log = logging.getLogger("query")


def redis_client(host: str, port: int, db: int, *, timeout_seconds: float, max_connections: int) -> Redis:
    pool = BlockingConnectionPool(host=host, port=port, db=db, max_connections=max_connections, timeout=timeout_seconds,
                                  socket_timeout=timeout_seconds, socket_connect_timeout=timeout_seconds,
                                  retry=Retry(NoBackoff(), 0), decode_responses=True)
    return Redis(connection_pool=pool, retry=Retry(NoBackoff(), 0))


def redis_from_settings() -> Redis:
    return redis_client(settings.redis_host, settings.redis_port, settings.redis_db,
                        timeout_seconds=settings.redis_timeout_seconds, max_connections=settings.redis_max_connections)
