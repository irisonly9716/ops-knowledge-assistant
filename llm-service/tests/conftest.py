"""Async tests run through anyio's pytest plugin (anyio already comes with FastAPI, so no extra dependency), on the
selector event loop: async psycopg can't run on ProactorEventLoop, the default on Windows."""
import asyncio

import pytest


@pytest.fixture
def anyio_backend():
    return "asyncio", {"loop_factory": asyncio.SelectorEventLoop}
