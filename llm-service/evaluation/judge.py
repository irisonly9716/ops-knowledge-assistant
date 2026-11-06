"""Grading an answer with a model, the way RAGAS does it, without RAGAS.

Why not RAGAS itself: it can't be installed next to this service. Its latest release (0.4.3) needs `instructor`, and
every `instructor` that accepts openai 3.x pins `jiter<0.15`, while the openai 3.29 used here needs `jiter>=0.16`. The
older 0.2 line needs a newer pydantic-settings and was written for LangChain 0.x. In an environment of its own it
installs (93 packages, 424 MB) but fails on import against the current langchain-community. The method is what
matters, and it is short:

  faithfulness   split the answer into standalone claims, then check each against the excerpts the answering model
                 was given: the share supported. It measures whether the answer sticks to its sources, not whether it
                 is right: a faithful answer from the wrong excerpts still scores 1.
  coverage       check each of the question's answer points against the answer: the share it says. This is RAGAS's
                 factual correctness in recall mode, with the reference already split into points by the review.

Every step is a separate call with a JSON schema, so a verdict is one word per claim and not prose to parse. The judge
is a different model from the one answering, so an answer isn't graded by the model that wrote it.
"""
import json
import re
from dataclasses import dataclass
from typing import Any, Protocol

_CITATION = re.compile(r"\s*\[\s*\d+(?:\s*,\s*\d+)*\s*\]")

CLAIMS_INSTRUCTIONS = """You split an answer into claims.

A claim is one statement of fact the answer makes, written so it can be understood alone: replace pronouns with what
they refer to, and keep numbers, names and conditions exactly as the answer has them. Don't add anything the answer
doesn't say, and don't merge separate facts into one claim. Ignore citation markers like [1]. A statement that the
documents don't cover something is a claim too."""

VERIFY_INSTRUCTIONS = """You check claims against numbered excerpts.

A claim is supported only if the excerpts state it or it follows directly from what they state. A claim that is
plausible, or true in general, but not in the excerpts is not supported. Judge each claim on its own, in order."""

COVERAGE_INSTRUCTIONS = """You check whether an answer says each of the points a correct answer has to make.

A point is covered if the answer states it, in any wording, with the same numbers, names and conditions. A point the
answer only hints at, or states with a different number or condition, is not covered. Judge each point on its own,
in order."""


def _verdicts_schema(name: str, verdicts: tuple[str, str]) -> dict:
    return {"type": "object", "additionalProperties": False, "required": [name], "properties": {name: {
        "type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["reason", "verdict"],
                                   "properties": {"reason": {"type": "string"},
                                                  "verdict": {"type": "string", "enum": list(verdicts)}}}}}}


CLAIMS_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["claims"],
                 "properties": {"claims": {"type": "array", "items": {"type": "string"}}}}
VERIFY_SCHEMA = _verdicts_schema("verdicts", ("supported", "not_supported"))
COVERAGE_SCHEMA = _verdicts_schema("verdicts", ("covered", "not_covered"))


class JudgeError(Exception):
    """The judge's reply can't be used: cut off, not the JSON asked for, or a verdict count that doesn't match."""


class JudgeModel(Protocol):
    async def json(self, instructions: str, prompt: str, name: str, schema: dict) -> dict: ...


@dataclass(frozen=True)
class Verdict:
    text: str        # the claim or the answer point
    ok: bool         # supported, or covered
    reason: str


@dataclass(frozen=True)
class Grade:
    claims: list[Verdict]
    points: list[Verdict]

    @property
    def faithfulness(self) -> float | None:
        """None when the answer makes no claim: there is nothing to be faithful or unfaithful about."""
        return sum(v.ok for v in self.claims) / len(self.claims) if self.claims else None

    @property
    def coverage(self) -> float | None:
        return sum(v.ok for v in self.points) / len(self.points) if self.points else None


def without_citations(text: str) -> str:
    return _CITATION.sub("", text)


def numbered(items: list[str]) -> str:
    return "\n".join(f"{i}. {item}" for i, item in enumerate(items, 1))


async def _verdicts(model: JudgeModel, instructions: str, prompt: str, schema: dict, items: list[str],
                    yes: str) -> list[Verdict]:
    reply = await model.json(instructions, prompt, "verdicts", schema)
    verdicts = reply["verdicts"]
    if len(verdicts) != len(items):
        raise JudgeError(f"asked about {len(items)} items, got {len(verdicts)} verdicts")
    return [Verdict(item, v["verdict"] == yes, v["reason"]) for item, v in zip(items, verdicts)]


async def grade(model: JudgeModel, question: str, answer: str, excerpts: list[str], answer_points: list[str]) -> Grade:
    answer = without_citations(answer)
    claims = (await model.json(CLAIMS_INSTRUCTIONS, f"Question: {question}\n\nAnswer:\n{answer}", "claims",
                               CLAIMS_SCHEMA))["claims"]
    claims = [c.strip() for c in claims if c.strip()]
    supported = []
    if claims:
        context = "\n\n".join(f"[{i}] {text}" for i, text in enumerate(excerpts, 1))
        supported = await _verdicts(model, VERIFY_INSTRUCTIONS, f"Excerpts:\n\n{context}\n\nClaims:\n{numbered(claims)}",
                                    VERIFY_SCHEMA, claims, "supported")
    covered = []
    if answer_points:
        covered = await _verdicts(model, COVERAGE_INSTRUCTIONS,
                                  f"Question: {question}\n\nAnswer:\n{answer}\n\nPoints:\n{numbered(answer_points)}",
                                  COVERAGE_SCHEMA, answer_points, "covered")
    return Grade(supported, covered)


class OpenAIJudge:
    """Structured output through the Responses API. Usage is added up so the run can say what grading cost."""

    def __init__(self, client: Any, model: str, timeout_seconds: float, max_output_tokens: int = 4000):
        self._client = client
        self.model = model
        self._timeout = timeout_seconds
        self._max_output_tokens = max_output_tokens
        self.input_tokens = 0
        self.output_tokens = 0

    async def json(self, instructions: str, prompt: str, name: str, schema: dict) -> dict:
        response = await self._client.responses.create(
            model=self.model, instructions=instructions, input=prompt, max_output_tokens=self._max_output_tokens,
            reasoning={"effort": "low"}, store=False, timeout=self._timeout,
            text={"format": {"type": "json_schema", "name": name, "schema": schema, "strict": True}})
        self.input_tokens += response.usage.input_tokens
        self.output_tokens += response.usage.output_tokens
        if response.status != "completed":
            raise JudgeError(f"the judge's reply was cut off ({response.status})")
        try:
            return json.loads(response.output_text)
        except ValueError as error:
            raise JudgeError(f"the judge's reply isn't JSON: {error}") from error
