"""Call the real embedding API once to see what goes in and what comes out. Costs a tiny amount
(a few dozen tokens, far below $0.0001).

Run:
    cd llm-service
    uv run python -m scripts.try_embedding
    uv run python -m scripts.try_embedding "Our livestream reach was suddenly limited" "What evidence does an appeal need"

Never prints the API key.
"""
import sys

from app.rag.embeddings import openai_embedder_from_settings

# Standard price of text-embedding-3-small: $0.02 per million tokens (OpenAI pricing page, 2026-10)
PRICE_PER_MILLION_TOKENS = 0.02

DEFAULT_TEXTS = [
    "Our livestream's reach was suddenly limited by the platform. What should we do?",
    "Account restriction handling: identify the triggered policy in the seller center, then file an appeal ticket.",
    "What should I have for lunch today?",
]


def dot(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


def main(texts: list[str]) -> None:
    embedder = openai_embedder_from_settings()
    batch = embedder.embed(texts)

    print(f"Model: {embedder.model}\n")
    for i, (text, vector) in enumerate(zip(texts, batch.vectors), start=1):
        print(f"[{i}] {text}")
        print(f"    {len(vector)} dimensions, first 5 values: {[round(x, 4) for x in vector[:5]]}")
        # OpenAI returns vectors normalized to length 1, so the dot product of two equals their cosine similarity
        print(f"    vector length: {dot(vector, vector) ** 0.5:.4f}")

    if len(texts) > 1:
        print("\nPairwise cosine similarity (closer to 1 = more similar):")
        for i in range(len(texts)):
            for j in range(i + 1, len(texts)):
                print(f"    [{i + 1}] vs [{j + 1}]: {dot(batch.vectors[i], batch.vectors[j]):.4f}")

    cost = batch.total_tokens * PRICE_PER_MILLION_TOKENS / 1_000_000
    print(f"\nBilled {batch.total_tokens} tokens, about ${cost:.8f}")


if __name__ == "__main__":
    main(sys.argv[1:] or DEFAULT_TEXTS)
