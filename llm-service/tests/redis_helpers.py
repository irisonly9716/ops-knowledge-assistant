"""A Redis for the tests: the compose one, in database 15, emptied before and after each test. Never database 0, the
query path's own, so a test run can't drop a developer's cached answers or conversations."""
import pytest
import redis
from redis.asyncio import Redis

from app.config import settings
from app.memory.redis_client import redis_client

TEST_DB = 15


def redis_available() -> bool:
    try:
        return redis.Redis(host=settings.redis_host, port=settings.redis_port, db=TEST_DB, socket_timeout=0.5,
                           socket_connect_timeout=0.5).ping()
    except redis.RedisError:
        return False


needs_redis = pytest.mark.skipif(not redis_available(), reason="needs the docker compose Redis")


@pytest.fixture
async def test_redis():
    client = Redis(host=settings.redis_host, port=settings.redis_port, db=TEST_DB, decode_responses=True)
    await client.flushdb()
    yield client
    await client.flushdb()
    await client.aclose()


def unreachable_redis() -> Redis:
    """A Redis nobody listens on, built as the query path builds its own: what a query sees when Redis is down."""
    return redis_client("127.0.0.1", 1, 0, timeout_seconds=0.1, max_connections=5)
