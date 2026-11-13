"""Which way a question goes: to the documents, to the campaign data, to both, or to neither. One model call decides,
with a reply of a fixed shape (structured output): the route, how sure the model is, and why.

    route        what runs
    documents    the document search
    data         Text-to-SQL
    both         both
    none         the document search: the router never refuses a question on its own. The document path's refusal
                 threshold and its model decide, so a wrong "none" can't lose an answerable question

Below router_min_confidence both paths run. The two mistakes don't cost the same: a path that should have run and
didn't loses half the answer or all of it, while one that ran for nothing costs a search and a model call.

When the model can't be asked in time, or its reply doesn't fit the shape, a keyword rule decides instead. It is
coarse on purpose: a fallback, and the baseline the model is measured against (evaluation/route_run.py).

The router sees the question alone, not who asks: which source holds an answer doesn't depend on the asker, and what
they may see is enforced on each path.
"""
import asyncio
import re
from dataclasses import dataclass
from typing import Literal

from openai import APIError
from pydantic import BaseModel, ConfigDict, Field

from app.rag.query_models import ModelBusy, StructuredModel
from app.time_budget import TimeBudget

DOCUMENTS, DATA = "documents", "data"
BOTH = frozenset({DOCUMENTS, DATA})
PATHS = {"documents": frozenset({DOCUMENTS}), "data": frozenset({DATA}), "both": BOTH, "none": frozenset({DOCUMENTS})}
MODEL, KEYWORDS = "model", "keywords"
# The reply is a route, a number and one sentence: about 25 tokens on dev. The limit only stops a runaway reason
MAX_OUTPUT_TOKENS = 200

INSTRUCTIONS = """You decide where the answer to a question from a live-commerce company's operations staff comes from.

The two sources:
- documents: the company's written knowledge. Standard operating procedures and guidelines (how to run a livestream, \
rules, checklists, who does what), campaign plans written before a campaign (targets, strategy, product line-up, \
budget), and post-campaign reviews written after it (what happened, why, lessons, what to change).
- data: the campaign database. Numbers per campaign, per day, per product and for a sample of customers: GMV, \
viewers, conversion rate, units sold, sales, returns, profit, operating costs and ROI, and the hosts with their \
followers. It holds what happened, and the budgeted operating cost; the targets and strategy set before a campaign \
are only in its plan, so a question comparing results with targets or with the plan needs both.

Routes:
- documents: the answer is in the written knowledge, and no number from the database is needed.
- data: the answer is numbers or a list from the database, and nothing written is needed.
- both: the answer needs numbers from the database and an explanation, a reason, a plan or advice from the documents. \
A question asking why a campaign did well or badly, or what to change next time given how the last one went, needs both.
- none: neither source holds it, such as general knowledge, personal matters, or anything outside the company's \
livestream operations.

confidence: how sure you are of the route, from 0 to 1. reason: one short sentence."""


class RouteChoice(BaseModel):
    """The shape of the model's reply."""
    model_config = ConfigDict(extra="forbid")
    route: Literal["documents", "data", "both", "none"]
    confidence: float = Field(description="how sure you are of the route, from 0 to 1")
    reason: str = Field(description="one short sentence")


@dataclass(frozen=True)
class Routing:
    route: str
    confidence: float | None   # None when the keyword rule decided
    reason: str
    paths: frozenset[str]
    decided_by: str            # model | keywords
    input_tokens: int = 0
    output_tokens: int = 0


def paths(route: str, confidence: float | None, min_confidence: float) -> frozenset[str]:
    """Both paths below min_confidence, and for a confidence above 1, which is no confidence at all."""
    if confidence is not None and not min_confidence <= confidence <= 1:
        return BOTH
    return PATHS[route]


# Words that point at numbers in the database, and at written guidance. Matched whole and in any case
_DATA_WORDS = re.compile(r"\b(gmv|conversion|roi|revenue|sales|sold|units|profit|margin|cost|viewers|audience|"
                         r"followers|dau|watch time|dwell|average|total|how many|how much|top|rank|highest|lowest|"
                         r"best-selling|return rate|returns|click-through|sell-through)\b", re.I)
_DOCUMENT_WORDS = re.compile(r"\b(how do|how should|how to|what should|why|procedure|process|policy|rule|checklist|"
                             r"sop|guideline|who|step|plan|review|lesson|should we|must|required|need to)\b", re.I)


def keyword_route(question: str) -> str:
    """Both kinds of words: both. Neither: documents, where a question nothing answers is refused."""
    data, documents = bool(_DATA_WORDS.search(question)), bool(_DOCUMENT_WORDS.search(question))
    return "both" if data and documents else "data" if data else "documents"


class QuestionRouter:
    def __init__(self, model: StructuredModel, *, min_confidence: float, timeout_seconds: float):
        self._model = model
        self._min_confidence = min_confidence
        self._timeout = timeout_seconds

    async def route(self, question: str, budget: TimeBudget) -> Routing:
        """Never fails: whatever goes wrong with the model, the keyword rule decides and the reason says what."""
        own = TimeBudget(max(min(self._timeout, budget.remaining()), 0))
        try:
            async with asyncio.timeout(own.remaining()):
                answer = await self._model.ask(INSTRUCTIONS, question, RouteChoice, own)
        except (TimeoutError, APIError, ModelBusy, ValueError) as error:   # ValueError: a reply the shape refused
            return self.by_keywords(question, f"the model didn't route it: {type(error).__name__}")
        choice = answer.value
        if not isinstance(choice, RouteChoice):
            return self.by_keywords(question, "the model's reply didn't complete", answer.input_tokens,
                                    answer.output_tokens)
        return Routing(choice.route, choice.confidence, choice.reason,
                       paths(choice.route, choice.confidence, self._min_confidence), MODEL, answer.input_tokens,
                       answer.output_tokens)

    @staticmethod
    def by_keywords(question: str, reason: str, input_tokens: int = 0, output_tokens: int = 0) -> Routing:
        route = keyword_route(question)
        return Routing(route, None, reason, PATHS[route], KEYWORDS, input_tokens, output_tokens)
