"""Grading an answer, with a fake judge model: what it is asked, and how its verdicts become scores."""
import pytest

from evaluation.judge import CLAIMS_INSTRUCTIONS, COVERAGE_INSTRUCTIONS, VERIFY_INSTRUCTIONS, JudgeError, grade


class FakeJudge:
    def __init__(self, claims, supported=(), covered=()):
        self.replies = {CLAIMS_INSTRUCTIONS: {"claims": claims},
                        VERIFY_INSTRUCTIONS: {"verdicts": [{"reason": "r", "verdict": v} for v in supported]},
                        COVERAGE_INSTRUCTIONS: {"verdicts": [{"reason": "r", "verdict": v} for v in covered]}}
        self.prompts = {}

    async def json(self, instructions, prompt, name, schema):
        self.prompts[instructions] = prompt
        return self.replies[instructions]


@pytest.mark.anyio
async def test_faithfulness_is_the_share_of_claims_the_excerpts_support():
    judge = FakeJudge(["A", "B", "C", "D"], supported=["supported", "not_supported", "supported", "supported"],
                      covered=["covered", "not_covered"])
    result = await grade(judge, "Q?", "A and B [1]. C, D [2].", ["first", "second"], ["p1", "p2"])
    assert result.faithfulness == 0.75
    assert result.coverage == 0.5
    assert [v.text for v in result.claims if not v.ok] == ["B"]
    assert [v.text for v in result.points if not v.ok] == ["p2"]


@pytest.mark.anyio
async def test_the_judge_sees_the_excerpts_the_answer_had_and_no_citation_markers():
    judge = FakeJudge(["A"], supported=["supported"], covered=["covered"])
    await grade(judge, "Q?", "First [1]. Then [1, 2][3].", ["excerpt one", "excerpt two"], ["p1"])
    assert "[1]" not in judge.prompts[CLAIMS_INSTRUCTIONS]
    assert "First. Then." in judge.prompts[CLAIMS_INSTRUCTIONS]
    assert "[1] excerpt one" in judge.prompts[VERIFY_INSTRUCTIONS]
    assert "[2] excerpt two" in judge.prompts[VERIFY_INSTRUCTIONS]
    assert "1. p1" in judge.prompts[COVERAGE_INSTRUCTIONS]


@pytest.mark.anyio
async def test_an_answer_with_no_claim_has_no_faithfulness_and_no_verification_call():
    judge = FakeJudge(["  "], covered=["not_covered"])
    result = await grade(judge, "Q?", "Hmm.", ["e"], ["p1"])
    assert result.faithfulness is None
    assert VERIFY_INSTRUCTIONS not in judge.prompts
    assert result.coverage == 0


@pytest.mark.anyio
async def test_a_verdict_count_that_does_not_match_is_an_error_not_a_score():
    judge = FakeJudge(["A", "B"], supported=["supported"])
    with pytest.raises(JudgeError, match="2 items, got 1"):
        await grade(judge, "Q?", "A. B.", ["e"], [])
