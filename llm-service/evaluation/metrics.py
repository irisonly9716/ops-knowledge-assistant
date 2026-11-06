"""The numbers of an evaluation run, from what each question went through. Pure functions: no database, no model.

Retrieval is scored by section, not by chunk: a label names a section, and a long section is several chunks. A
section counts as found at the rank of its best chunk.

  Recall@k   the share of a question's sections with a chunk in the first k, averaged over questions
  MRR        1 / the rank of the first chunk of any of its sections, 0 when none is in the candidates

Answers are scored on what a user would see. Refusing is never wrong for a question to refuse, and answering one is;
for a question to answer it is the other way round. An error (an answer that failed validation, a timeout) shows the
user nothing, so it counts as not answering.

The refusal threshold is chosen from one run made with the threshold at 0, where every question reached the model. At
a threshold t, a question whose best chunk scores below t is refused before the model is called, and every other
question ends as it did in the run. So the whole sweep comes from one run, without asking the model again.
"""
import math
import random
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass

ANSWERED = "answered"
REFUSED = "refused"
ERROR = "error"


# ---------------------------------------------------------------------------
# Retrieval
# ---------------------------------------------------------------------------
def section_ranks(candidate_ids: Sequence[int], sections: Sequence[Iterable[int]]) -> list[int | None]:
    """For each section, the 1-based rank of its best chunk among the candidates, or None if none of them is there."""
    rank_of = {chunk_id: rank for rank, chunk_id in reversed(list(enumerate(candidate_ids, 1)))}
    ranks = []
    for chunk_ids in sections:
        found = [rank_of[c] for c in chunk_ids if c in rank_of]
        ranks.append(min(found) if found else None)
    return ranks


def recall_at(ranks: Sequence[int | None], k: int) -> float:
    if not ranks:
        raise ValueError("a question without labelled sections has no recall")
    return sum(1 for rank in ranks if rank is not None and rank <= k) / len(ranks)


def reciprocal_rank(ranks: Sequence[int | None]) -> float:
    found = [rank for rank in ranks if rank is not None]
    return 1 / min(found) if found else 0.0


@dataclass(frozen=True)
class RetrievalScores:
    questions: int
    recall_at_5: float
    recall_at_50: float
    mrr: float


def retrieval_scores(all_ranks: Sequence[Sequence[int | None]]) -> RetrievalScores | None:
    if not all_ranks:
        return None
    n = len(all_ranks)
    return RetrievalScores(n, sum(recall_at(r, 5) for r in all_ranks) / n, sum(recall_at(r, 50) for r in all_ranks) / n,
                           sum(reciprocal_rank(r) for r in all_ranks) / n)


# ---------------------------------------------------------------------------
# Refusals and the threshold
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Outcome:
    """One question of a run made with the threshold at 0."""
    expected: str        # answer | refuse
    status: str          # answered | refused | error, as it ended in the run
    top_score: float     # the similarity of the best chunk the prompt got


def status_at(outcome: Outcome, threshold: float) -> str:
    return REFUSED if outcome.top_score < threshold else outcome.status


@dataclass(frozen=True)
class Rates:
    threshold: float
    to_refuse: int
    false_answers: int     # questions to refuse that were answered
    to_answer: int
    false_refusals: int    # questions to answer that were refused or ended in an error

    @property
    def false_answer_rate(self) -> float:
        return self.false_answers / self.to_refuse if self.to_refuse else 0.0

    @property
    def false_refusal_rate(self) -> float:
        return self.false_refusals / self.to_answer if self.to_answer else 0.0

    def cost(self, wrong_answer_cost: float) -> float:
        """Rates, not counts: how often each kind of question comes up in real use isn't known, and dev's mix of them
        is an artifact of how it was written. Weighing the rates assumes the two kinds are about as common."""
        return wrong_answer_cost * self.false_answer_rate + self.false_refusal_rate


def rates_at(outcomes: Sequence[Outcome], threshold: float) -> Rates:
    to_refuse = [o for o in outcomes if o.expected == "refuse"]
    to_answer = [o for o in outcomes if o.expected == "answer"]
    return Rates(threshold, len(to_refuse), sum(status_at(o, threshold) == ANSWERED for o in to_refuse),
                 len(to_answer), sum(status_at(o, threshold) != ANSWERED for o in to_answer))


def sweep(outcomes: Sequence[Outcome], thresholds: Iterable[float]) -> list[Rates]:
    return [rates_at(outcomes, t) for t in thresholds]


def threshold_grid(low: float = 0.0, high: float = 0.7, step: float = 0.01) -> list[float]:
    return [round(low + i * step, 4) for i in range(int(round((high - low) / step)) + 1)]


def choose_threshold(rates: Sequence[Rates], wrong_answer_cost: float) -> float:
    """The middle of the widest run of thresholds with the lowest cost. With few questions the cost moves in steps, and
    every threshold inside a step scores the same: the middle is the furthest from both edges, where one more question
    on the other side would change the result."""
    if not rates:
        raise ValueError("nothing to choose from")
    best = min(r.cost(wrong_answer_cost) for r in rates)
    runs: list[list[float]] = []
    for r in rates:
        if math.isclose(r.cost(wrong_answer_cost), best):
            if runs and runs[-1][-1] == previous:
                runs[-1].append(r.threshold)
            else:
                runs.append([r.threshold])
        previous = r.threshold
    widest = max(runs, key=len)   # the first of equally wide runs: the lower threshold refuses less
    return round(widest[len(widest) // 2], 4)


# ---------------------------------------------------------------------------
# Latency and cost
# ---------------------------------------------------------------------------
def percentile(values: Sequence[float], p: float) -> float:
    """Nearest rank: always a value that was measured, which matters with few samples."""
    if not values:
        raise ValueError("no values")
    ordered = sorted(values)
    return ordered[max(math.ceil(p / 100 * len(ordered)) - 1, 0)]


def mean(values: Iterable[float]) -> float | None:
    values = list(values)
    return sum(values) / len(values) if values else None


def sample(items: Sequence, size: int, seed: str, key: Callable = lambda item: item) -> list:
    """The same items for the same seed, whatever order they came in."""
    ordered = sorted(items, key=key)
    return random.Random(seed).sample(ordered, min(size, len(ordered)))
