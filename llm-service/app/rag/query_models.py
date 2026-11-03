"""The two model calls of a query: embedding the question and writing the answer. Both are async, so a waiting
query costs the event loop nothing; a sync SDK call here would stop every other query in this process.

Ingestion keeps the sync client in embeddings.py: a consumer handles one message at a time. Which failures are
worth retrying, and how long to wait, is shared with it (embeddings.retry_wait). As there, the SDK's own retries
are off and every retry has to fit the query's time budget.
"""
import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol

from openai import APIConnectionError, APIStatusError, AsyncOpenAI

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


class QueryEmbedder(Protocol):
    model: str

    async def embed_question(self, question: str, budget: TimeBudget) -> list[float]: ...


class AnswerModel(Protocol):
    async def answer(self, instructions: str, prompt: str, budget: TimeBudget) -> ModelAnswer: ...


async def _with_retries(send: Callable[[float], Awaitable[Any]], budget: TimeBudget, request_timeout: float,
                        sleep: Callable[[float], Awaitable[None]]) -> Any:
    failures = 0
    while True:
        remaining = budget.remaining()
        if remaining <= 0:
            raise TimeoutError("the time budget ran out before the model request could be sent")
        try:
            return await send(min(request_timeout, remaining))
        except (APIConnectionError, APIStatusError) as error:   # includes APITimeoutError
            failures += 1
            wait = retry_wait(error, failures)
            if wait >= budget.remaining():
                raise   # waiting would use up the budget anyway: give up now, with the real reason
            log.warning("model request failed (%s), retry %d in %.1f s", type(error).__name__, failures, wait)
            await sleep(wait)


class OpenAIQueryEmbedder:
    def __init__(self, client: AsyncOpenAI, model: str, request_timeout: float,
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep):
        self._client = client
        self.model = model
        self._request_timeout = request_timeout
        self._sleep = sleep

    async def embed_question(self, question: str, budget: TimeBudget) -> list[float]:
        async def send(timeout: float) -> Any:
            return await self._client.embeddings.create(model=self.model, input=[question],
                                                        dimensions=EMBEDDING_DIMENSIONS, timeout=timeout)
        response = await _with_retries(send, budget, self._request_timeout, self._sleep)
        return response.data[0].embedding


class OpenAIAnswerModel:
    def __init__(self, client: AsyncOpenAI, model: str, request_timeout: float, max_output_tokens: int,
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep):
        self._client = client
        self._model = model
        self._request_timeout = request_timeout
        self._max_output_tokens = max_output_tokens
        self._sleep = sleep

    async def answer(self, instructions: str, prompt: str, budget: TimeBudget) -> ModelAnswer:
        async def send(timeout: float) -> Any:
            return await self._client.responses.create(
                model=self._model, instructions=instructions, input=prompt, max_output_tokens=self._max_output_tokens,
                reasoning={"effort": "none"},   # answering from given excerpts needs no reasoning, and it is slower
                store=False,                    # internal documents don't stay with the provider as saved responses
                timeout=timeout,
            )
        response = await _with_retries(send, budget, self._request_timeout, self._sleep)
        return ModelAnswer(text=response.output_text, complete=response.status == "completed",
                           input_tokens=response.usage.input_tokens, output_tokens=response.usage.output_tokens)


def openai_client_from_settings() -> AsyncOpenAI:
    if not settings.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY is not set: add it to .env at the repository root (.env is not in git)")
    return AsyncOpenAI(api_key=settings.openai_api_key, base_url=settings.openai_base_url, max_retries=0)
