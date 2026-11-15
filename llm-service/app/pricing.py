"""What model calls cost: the audit's cost of a question (app/audit.py) and the evaluation's cost of a run."""
from collections.abc import Iterable
from decimal import Decimal

# OpenAI's prices per million tokens, as recorded in scripts/generate_corpus.py, scripts/generate_questions.py and
# scripts/try_embedding.py. An audit row keeps the cost at the prices it ran at, so a change here doesn't rewrite it
PRICE_PER_MILLION = {
    "gpt-6-luna": {"input": Decimal("0.10"), "output": Decimal("0.50")},
    "gpt-6-sol": {"input": Decimal("2.00"), "output": Decimal("10.00")},
    "text-embedding-3-small": {"input": Decimal("0.02"), "output": Decimal("0")},
}


def price(model: str, input_tokens: int, output_tokens: int) -> Decimal:
    """KeyError for a model with no price: an evaluation must not report a cost it doesn't know."""
    p = PRICE_PER_MILLION[model]
    return (Decimal(input_tokens) * p["input"] + Decimal(output_tokens) * p["output"]) / 1_000_000


def cost(calls: Iterable[tuple[str, int, int]]) -> Decimal | None:
    """The cost of (model, input tokens, output tokens) calls; None when one of them used a model with no price, so a
    model configured without one leaves the cost unknown instead of failing the question it answers."""
    total = Decimal(0)
    for model, input_tokens, output_tokens in calls:
        if not (input_tokens or output_tokens):
            continue
        if model not in PRICE_PER_MILLION:
            return None
        total += price(model, input_tokens, output_tokens)
    return total
