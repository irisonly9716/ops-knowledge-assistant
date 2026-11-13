"""The model calls of a query: embedding the question, writing the answer, and replies of a fixed shape such as the
route a question takes. All async, so a waiting query costs the event loop nothing; a sync SDK call here would stop
every other query in this process.

Ingestion keeps the sync client in embeddings.py: a consumer handles one message at a time. Which failures are
worth retrying, and how long to wait, is shared with it (embeddings.retry_wait). As there, the SDK's own retries
are off and every retry has to fit the query's time budget.
"""
import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, Protocol

import httpx
from openai import APIConnectionError, APIStatusError, AsyncOpenAI, DefaultAsyncHttpxClient
from pydantic import BaseModel

from app.config import settings
from app.rag.embeddings import EMBEDDING_DIMENSIONS, retry_wait
from app.time_budget import TimeBudget

log = logging.getLogger("query")


@dataclass(frozen=True)
class ModelAnswer:
    text: str
    complete: bool       # False: cut off, for example by the output limit
    input_tokens: int
    output_tokens: int


@dataclass(frozen=True)
class StructuredAnswer:
    value: Any | None    # an instance of the shape asked for; None when the reply didn't complete or wasn't one
    input_tokens: int
    output_tokens: int


class QueryEmbedder(Protocol):
    model: str

    async def embed_question(self, question: str, budget: TimeBudget) -> list[float]: ...


class AnswerModel(Protocol):
    async def answer(self, instructions: str, prompt: str, budget: TimeBudget) -> ModelAnswer: ...


class StructuredModel(Protocol):
    async def ask(self, instructions: str, prompt: str, shape: type[BaseModel],
                  budget: TimeBudget) -> StructuredAnswer: ...


class ModelBusy(Exception):
    """Every model call slot is taken and none came free in time."""


class ModelCallLimit:
    """How many model calls may run at once in this process. The rate limit in the backend controls how many queries
    come in; this protects against what it can't see, such as the model slowing down: at 6 s per answer, 300 queries a
    second would become 1,800 calls at once. A query that can't get a slot soon is refused instead of queueing until
    its time runs out, so the client hears at once and the queue can't grow without limit."""

    def __init__(self, max_calls: int, max_wait_seconds: float):
        self._semaphore = asyncio.Semaphore(max_calls)
        self._max_wait = max_wait_seconds

    @asynccontextmanager
    async def slot(self, budget: TimeBudget) -> AsyncIterator[None]:
        try:
            async with asyncio.timeout(max(min(self._max_wait, budget.remaining()), 0)):
                await self._semaphore.acquire()
        except TimeoutError:
            raise ModelBusy("every model call slot is taken") from None
        try:
            yield
        finally:
            self._semaphore.release()


async def _with_retries(send: Callable[[float], Awaitable[Any]], limit: ModelCallLimit, budget: TimeBudget,
                        request_timeout: float, sleep: Callable[[float], Awaitable[None]]) -> Any:
    failures = 0
    while True:
        if budget.remaining() <= 0:
            raise TimeoutError("the time budget ran out before the model request could be sent")
        try:
            async with limit.slot(budget):
                remaining = budget.remaining()   # after any wait for the slot
                if remaining <= 0:
                    raise TimeoutError("the time budget ran out before the model request could be sent")
                return await send(min(request_timeout, remaining))
        # The slot is back by now: waiting to retry holds none, or a rate-limited provider would use up every slot
        # with queries that are only sleeping
        except (APIConnectionError, APIStatusError) as error:   # includes APITimeoutError
            failures += 1
            wait = retry_wait(error, failures)
            if wait >= budget.remaining():
                raise   # waiting would use up the budget anyway: give up now, with the real reason
            log.warning("model request failed (%s), retry %d in %.1f s", type(error).__name__, failures, wait)
            await sleep(wait)


class OpenAIQueryEmbedder:
    def __init__(self, client: AsyncOpenAI, model: str, limit: ModelCallLimit, request_timeout: float,
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep):
        self._client = client
        self.model = model
        self._limit = limit
        self._request_timeout = request_timeout
        self._sleep = sleep

    async def embed_question(self, question: str, budget: TimeBudget) -> list[float]:
        async def send(timeout: float) -> Any:
            return await self._client.embeddings.create(model=self.model, input=[question],
                                                        dimensions=EMBEDDING_DIMENSIONS, timeout=timeout)
        response = await _with_retries(send, self._limit, budget, self._request_timeout, self._sleep)
        return response.data[0].embedding


class OpenAIAnswerModel:
    """reasoning_effort: "none" unless a setting says otherwise. Answering from given excerpts or rows needs no
    reasoning, and reasoning is slower; whether advice from documents and data together gains from it is for the
    evaluation to show, which is why that one is a setting (config.py)."""

    def __init__(self, client: AsyncOpenAI, model: str, limit: ModelCallLimit, request_timeout: float,
                 max_output_tokens: int, sleep: Callable[[float], Awaitable[None]] = asyncio.sleep, *,
                 reasoning_effort: str = "none"):
        self._client = client
        self._model = model
        self._limit = limit
        self._request_timeout = request_timeout
        self._max_output_tokens = max_output_tokens
        self._sleep = sleep
        self._reasoning_effort = reasoning_effort

    async def answer(self, instructions: str, prompt: str, budget: TimeBudget) -> ModelAnswer:
        async def send(timeout: float) -> Any:
            return await self._client.responses.create(
                model=self._model, instructions=instructions, input=prompt, max_output_tokens=self._max_output_tokens,
                reasoning={"effort": self._reasoning_effort},
                store=False,                    # internal documents don't stay with the provider as saved responses
                timeout=timeout,
            )
        response = await _with_retries(send, self._limit, budget, self._request_timeout, self._sleep)
        return ModelAnswer(text=response.output_text, complete=response.status == "completed",
                           input_tokens=response.usage.input_tokens, output_tokens=response.usage.output_tokens)


class OpenAIStructuredModel:
    """A reply that code reads, in the shape of a Pydantic model (structured output). The provider holds the reply to
    the shape's JSON schema, so a field can't be missing or a value outside an enum; what is left to check here is
    whether the reply completed. Free text would need parsing, and a model replying "Route: Data." instead of "data"
    would break it."""

    def __init__(self, client: AsyncOpenAI, model: str, limit: ModelCallLimit, request_timeout: float,
                 max_output_tokens: int, sleep: Callable[[float], Awaitable[None]] = asyncio.sleep):
        self._client = client
        self._model = model
        self._limit = limit
        self._request_timeout = request_timeout
        self._max_output_tokens = max_output_tokens
        self._sleep = sleep

    async def ask(self, instructions: str, prompt: str, shape: type[BaseModel], budget: TimeBudget) -> StructuredAnswer:
        async def send(timeout: float) -> Any:
            return await self._client.responses.parse(
                model=self._model, instructions=instructions, input=prompt, text_format=shape,
                max_output_tokens=self._max_output_tokens, reasoning={"effort": "none"}, store=False, timeout=timeout,
            )
        response = await _with_retries(send, self._limit, budget, self._request_timeout, self._sleep)
        value = response.output_parsed if response.status == "completed" else None
        return StructuredAnswer(value, response.usage.input_tokens, response.usage.output_tokens)


def openai_client_from_settings() -> AsyncOpenAI:
    if not settings.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY is not set: add it to .env at the repository root (.env is not in git)")
    # The SDK defaults to 1,000 connections, keeping 100 alive. That would be a second, hidden limit below the 500 QPS
    # target, and connections it can't keep have to be set up again for the next call
    connections = settings.model_max_concurrent_calls
    http_client = DefaultAsyncHttpxClient(limits=httpx.Limits(max_connections=connections, max_keepalive_connections=connections))
    return AsyncOpenAI(api_key=settings.openai_api_key, base_url=settings.openai_base_url, max_retries=0, http_client=http_client)
