"""Which way a question goes: the paths each route runs, the keyword rule, and the router falling back to it whenever the
model can't decide, with a fake model."""
import asyncio

import httpx
import openai
import pytest

from app.rag.query_models import ModelBusy, StructuredAnswer
from app.router import routing
from app.router.rewriting import Turn
from app.router.routing import BOTH, DATA, DOCUMENTS, QuestionRouter, RouteChoice
from app.time_budget import TimeBudget


def test_each_route_runs_its_paths_and_none_goes_to_the_documents():
    assert routing.paths("documents", 0.9, 0.6) == {DOCUMENTS}
    assert routing.paths("data", 0.9, 0.6) == {DATA}
    assert routing.paths("both", 0.9, 0.6) == BOTH
    assert routing.paths("none", 0.9, 0.6) == {DOCUMENTS}   # the router never refuses on its own


def test_below_the_threshold_or_out_of_range_both_paths_run():
    assert routing.paths("data", 0.59, 0.6) == BOTH
    assert routing.paths("data", 0.6, 0.6) == {DATA}
    assert routing.paths("documents", 1.5, 0.6) == BOTH
    assert routing.paths("documents", -0.1, 0.0) == BOTH
    assert routing.paths("data", None, 0.6) == {DATA}   # the keyword rule has no confidence to doubt


@pytest.mark.parametrize("question, route", [
    ("What was the GMV of the summer sale?", "data"),
    ("How many units of serum did we sell?", "data"),
    ("Who signs off on the stream plan?", "documents"),
    ("What is the checklist before going live?", "documents"),
    ("Why did conversion drop, and what should we change?", "both"),
    ("Tell me about the weather.", "documents"),   # nothing matches: the document path refuses what nothing answers
])
def test_the_keyword_rule_routes_numbers_to_data_and_guidance_to_documents(question, route):
    assert routing.keyword_route(question) == route


class FakeModel:
    def __init__(self, reply=None, error=None, delay=0.0):
        self.reply, self.error, self.delay = reply, error, delay
        self.asked = []

    async def ask(self, instructions, prompt, shape, budget):
        self.asked.append((instructions, prompt, shape, budget.remaining()))
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error:
            raise self.error
        return StructuredAnswer(self.reply, 300, 25)


def router(model, min_confidence=0.6, timeout=2.0):
    return QuestionRouter(model, min_confidence=min_confidence, timeout_seconds=timeout)


@pytest.mark.anyio
async def test_the_model_is_asked_with_the_question_alone_and_its_choice_is_used():
    model = FakeModel(RouteChoice(route="both", confidence=0.8, reason="Needs numbers and the review."))
    decided = await router(model).route("Why did the sale miss its target?", TimeBudget(10))
    instructions, prompt, shape, _ = model.asked[0]
    assert prompt == "Why did the sale miss its target?" and shape is RouteChoice and "post-campaign reviews" in instructions
    assert (decided.route, decided.confidence, decided.paths, decided.decided_by) == ("both", 0.8, BOTH, "model")
    assert (decided.input_tokens, decided.output_tokens) == (300, 25)


@pytest.mark.anyio
async def test_an_unsure_model_gets_both_paths():
    model = FakeModel(RouteChoice(route="data", confidence=0.4, reason="Probably numbers."))
    assert (await router(model).route("How did it go?", TimeBudget(10))).paths == BOTH


@pytest.mark.anyio
@pytest.mark.parametrize("error", [
    openai.APIConnectionError(request=httpx.Request("POST", "https://api.test")),
    ModelBusy("every model call slot is taken"),
    ValueError("the reply doesn't fit the shape"),
])
async def test_when_the_model_fails_the_keyword_rule_decides(error):
    decided = await router(FakeModel(error=error)).route("What was the GMV?", TimeBudget(10))
    assert (decided.route, decided.paths, decided.decided_by, decided.confidence) == ("data", {DATA}, "keywords", None)
    assert type(error).__name__ in decided.reason


@pytest.mark.anyio
async def test_a_slow_model_is_cut_off_at_the_routers_own_limit():
    started = asyncio.get_running_loop().time()
    decided = await router(FakeModel(RouteChoice(route="data", confidence=0.9, reason="x"), delay=5),
                           timeout=0.2).route("What was the GMV?", TimeBudget(10))
    assert decided.decided_by == "keywords" and "TimeoutError" in decided.reason
    assert asyncio.get_running_loop().time() - started < 1


@pytest.mark.anyio
async def test_the_router_takes_no_more_than_what_is_left_of_the_query():
    model = FakeModel(RouteChoice(route="data", confidence=0.9, reason="x"))
    await router(model, timeout=2.0).route("What was the GMV?", TimeBudget(0.5))
    assert model.asked[0][3] <= 0.5


@pytest.mark.anyio
async def test_a_reply_that_did_not_complete_falls_back_but_still_counts_its_tokens():
    decided = await router(FakeModel(None)).route("Who approves the plan?", TimeBudget(10))
    assert (decided.route, decided.decided_by, decided.input_tokens) == ("documents", "keywords", 300)


@pytest.mark.anyio
async def test_a_follow_up_is_routed_with_the_turns_before_it():
    model = FakeModel(RouteChoice(route="data", confidence=0.99, reason="Numbers about a campaign."))
    turns = (Turn("user", "When did the Electronics Black Friday Live campaign run?"),
             Turn("assistant", "It ran from 2025-11-16 to 2025-12-12."))
    await router(model).route("And the Beauty one?", TimeBudget(10), turns)
    prompt = model.asked[0][1]
    assert prompt.startswith("Conversation so far:\nuser: When did the Electronics")
    assert prompt.endswith("\n\nQuestion: And the Beauty one?")
