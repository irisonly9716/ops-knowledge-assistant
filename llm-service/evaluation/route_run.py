"""How well each way of routing sends a split's questions where their answers are, on the same questions: the keyword
rule, the nearest labelled questions by embedding, the model (app/router), and a cascade that asks the model only when
the nearest questions disagree.

    uv run python -m evaluation.route_run --split dev --name 1

Every question says which route it needs (eval/README.md). Scored three ways, because the mistakes don't cost the same:
  - accuracy: the route given is the route needed
  - a path missing: what runs (routing.paths) lacks a path the answer needs. This mistake loses the answer, or half of it
  - a path extra: what runs includes one it doesn't need, which costs a search and a model call
A question nothing answers needs no path, so the document path that runs for it counts as extra.

The nearest questions: each question's embedding against every other question's of the split, leaving out its own group
(questions written from the same section or the same SQL), whose wording would give its route away; the 5 closest vote.
Follow-ups are asked without the turns before them, as the query path has no conversation memory yet, and are also
reported apart.

The model is asked with the query path's settings (router_model, router_timeout_seconds); the keyword rule decides
when it doesn't reply in time, as on the query path. Its threshold is swept here, so the run records the raw
confidence. Writes eval/results/<split>-route-<name>.jsonl and .md.
"""
import argparse
import asyncio
import json
import math
import time
from collections import Counter
from dataclasses import dataclass
from decimal import Decimal

from app import db
from app.config import settings
from app.rag.embeddings import embed_in_batches, openai_embedder_from_settings
from app.rag.query_models import ModelCallLimit, OpenAIStructuredModel, openai_client_from_settings
from app.router import routing
from app.router.routing import BOTH, DATA, DOCUMENTS, QuestionRouter
from app.server import choose_event_loop
from app.time_budget import TimeBudget
from evaluation import metrics
from evaluation.dataset import Question, load
from evaluation.run import EVAL_DIR, RESULTS_DIR, pct, price

ROUTES = ("documents", "data", "both", "none")
NEEDED = {"documents": frozenset({DOCUMENTS}), "data": frozenset({DATA}), "both": BOTH, "none": frozenset()}
NEIGHBOURS = 5
THRESHOLDS = (0.0, 0.5, 0.6, 0.7, 0.8, 0.9, 0.95)


@dataclass(frozen=True)
class Vote:
    route: str
    agreement: float          # the share of the neighbours that voted for it
    neighbours: tuple[str, ...]


def nearest(questions: list[Question], vectors: list[list[float]], k: int = NEIGHBOURS) -> list[Vote]:
    """For each question, the route most of its k closest questions need, outside its own group. A tie goes to the
    route whose voters are closer in all."""
    norms = [math.sqrt(sum(x * x for x in v)) for v in vectors]
    votes = []
    for i, q in enumerate(questions):
        scored = sorted(((sum(a * b for a, b in zip(vectors[i], vectors[j])) / (norms[i] * norms[j]), j)
                         for j, other in enumerate(questions) if j != i and other.group != q.group), reverse=True)[:k]
        weight = Counter()
        count = Counter()
        for similarity, j in scored:
            count[questions[j].route] += 1
            weight[questions[j].route] += similarity
        route = max(count, key=lambda r: (count[r], weight[r]))
        votes.append(Vote(route, count[route] / len(scored), tuple(questions[j].id for _, j in scored)))
    return votes


def scored(needed: str, chosen: frozenset[str]) -> tuple[bool, bool]:
    """(a path missing, a path extra)."""
    return bool(NEEDED[needed] - chosen), bool(chosen - NEEDED[needed])


async def ask_model(router: QuestionRouter, question: Question) -> dict:
    started = time.perf_counter()
    decided = await router.route(question.question, TimeBudget(settings.query_budget_seconds))
    return {"route": decided.route, "confidence": decided.confidence, "reason": decided.reason,
            "decided_by": decided.decided_by, "ms": (time.perf_counter() - started) * 1000,
            "input_tokens": decided.input_tokens, "output_tokens": decided.output_tokens,
            "cost_usd": str(price(settings.router_model, decided.input_tokens, decided.output_tokens))}


def model_paths(record: dict, threshold: float) -> frozenset[str]:
    m = record["model"]
    return routing.paths(m["route"], m["confidence"], threshold) if m["decided_by"] == routing.MODEL \
        else routing.PATHS[m["route"]]


def cascade(record: dict, agreement: float) -> tuple[str, frozenset[str], bool]:
    """The nearest questions' route when at least `agreement` of them agree, else the model's. (route, paths, asked)."""
    vote = record["nearest"]
    if vote["agreement"] >= agreement:
        return vote["route"], routing.PATHS[vote["route"]], False
    return record["model"]["route"], model_paths(record, 0.0), True


def methods(records: list[dict], threshold: float) -> dict[str, list[tuple[str, frozenset[str], bool]]]:
    """For each way of routing: per question, the route it gave, the paths that would run, and whether it asked the
    model."""
    return {
        "keyword rule": [(r["keyword"], routing.PATHS[r["keyword"]], False) for r in records],
        f"nearest {NEIGHBOURS} questions": [(r["nearest"]["route"], routing.PATHS[r["nearest"]["route"]], False)
                                            for r in records],
        "model": [(r["model"]["route"], model_paths(r, 0.0), True) for r in records],
        f"model, both below {threshold:g}": [(r["model"]["route"], model_paths(r, threshold), True) for r in records],
        "cascade: nearest when all 5 agree": [cascade(r, 1.0) for r in records],
        "cascade: nearest when 4 of 5 agree": [cascade(r, 0.8) for r in records],
    }


def row(label: str, records: list[dict], given: list[tuple[str, frozenset[str], bool]]) -> str:
    n = len(records)
    right = sum(route == r["route_needed"] for r, (route, _, _) in zip(records, given))
    missing = sum(scored(r["route_needed"], paths)[0] for r, (_, paths, _) in zip(records, given))
    extra = sum(scored(r["route_needed"], paths)[1] for r, (_, paths, _) in zip(records, given))
    asked = sum(a for _, _, a in given)
    return f"| {label} | {pct(right / n)} | {missing} ({pct(missing / n)}) | {extra} ({pct(extra / n)}) | {asked} |"


def confusion(records: list[dict], given: list[str]) -> list[str]:
    counts = Counter(zip((r["route_needed"] for r in records), given))
    lines = ["| needed \\ given | " + " | ".join(ROUTES) + " |", "|---" * (len(ROUTES) + 1) + "|"]
    lines += [f"| **{needed}** | " + " | ".join(str(counts[(needed, g)] or "·") for g in ROUTES) + " |"
              for needed in ROUTES]
    return lines


def summarize(records: list[dict], split: str, name: str, threshold: float, run_seconds: float) -> str:
    model = [r["model"] for r in records]
    by_model = [m for m in model if m["decided_by"] == routing.MODEL]
    follow_ups = [r for r in records if "multi_turn" in r["tags"]]
    standalone = [r for r in records if "multi_turn" not in r["tags"]]
    needed = Counter(r["route_needed"] for r in records)
    cost = sum((Decimal(m["cost_usd"]) for m in model), Decimal(0))
    times = [m["ms"] for m in model]
    lines = [f"# Routing run: {split}-route-{name}", "",
             f"{len(records)} questions of `eval/{split}.jsonl` ("
             + ", ".join(f"{needed[r]} {r}" for r in ROUTES) + f"), {len(follow_ups)} of them follow-ups asked without "
             f"the turns before them. The model: {settings.router_model}, {settings.router_timeout_seconds:g} s before "
             f"the keyword rule decides; {len(by_model)} of {len(records)} decided by the model. {run_seconds:.0f} s.", "",
             "## Every way of routing", "",
             "A path missing loses the answer or half of it; a path extra costs a search and a model call.", ""]
    for label, group in (("All questions", records), ("Without the follow-ups", standalone)):
        given = methods(group, threshold)
        lines += [f"**{label}** ({len(group)})", "",
                  "| Routing | Accuracy | A path missing | A path extra | Model calls |", "|---|---|---|---|---|"]
        lines += [row(method, group, g) for method, g in given.items()]
        lines.append("")

    lines += ["## The model's threshold", "",
              "Below the threshold both paths run. Without the follow-ups.", "",
              "| Both below | A path missing | A path extra |", "|---|---|---|"]
    for t in THRESHOLDS:
        missing = sum(scored(r["route_needed"], model_paths(r, t))[0] for r in standalone)
        extra = sum(scored(r["route_needed"], model_paths(r, t))[1] for r in standalone)
        lines.append(f"| {t:g} | {missing} | {extra} |")

    lines += ["", "## Is the model's confidence worth anything", "",
              "Accuracy by the confidence the model gave, without the follow-ups.", "",
              "| Confidence | Questions | Accuracy |", "|---|---|---|"]
    buckets = (("below 0.7", 0, 0.7), ("0.7 to 0.9", 0.7, 0.9), ("0.9 and above", 0.9, 1.01))
    for label, low, high in buckets:
        group = [r for r in standalone if r["model"]["confidence"] is not None and low <= r["model"]["confidence"] < high]
        if group:
            lines.append(f"| {label} | {len(group)} | "
                         f"{pct(sum(r['model']['route'] == r['route_needed'] for r in group) / len(group))} |")

    for label, given in (("the model", [m["route"] for m in model]), ("the keyword rule", [r["keyword"] for r in records]),
                         (f"the nearest {NEIGHBOURS} questions", [r["nearest"]["route"] for r in records])):
        lines += ["", f"## Confusion matrix: {label}", "", "Rows: the route needed; columns: the route given.", ""]
        lines += confusion(records, given)

    lines += ["", "## Where the model went wrong", "", "| Question | Needed | Given | Confidence | Its reason |",
              "|---|---|---|---|---|"]
    lines += [f"| `{r['id']}` {r['question'].replace('|', '/')} | {r['route_needed']} | {r['model']['route']} | "
              f"{r['model']['confidence'] if r['model']['confidence'] is not None else 'keywords'} | "
              f"{(r['model']['reason'] or '').replace('|', '/')} |"
              for r in records if r["model"]["route"] != r["route_needed"]]
    lines += ["", "## Latency and cost", "",
              f"The model: p50 {metrics.percentile(times, 50):.0f} ms, p95 {metrics.percentile(times, 95):.0f} ms, max "
              f"{max(times):.0f} ms a question; ${cost:.4f} in all, ${cost / len(records):.6f} a question, "
              f"{metrics.mean(m['input_tokens'] for m in model):.0f} input and "
              f"{metrics.mean(m['output_tokens'] for m in model):.0f} output tokens on average. The keyword rule and the "
              "nearest questions add no model call; the nearest use the question's embedding, which the query path makes "
              "for the document search anyway.", ""]
    return "\n".join(lines)


async def main_async(args: argparse.Namespace) -> None:
    db.require_compatible_event_loop()
    if not settings.openai_api_key:
        raise SystemExit("OPENAI_API_KEY is not set: add it to .env at the repository root")
    questions = load(EVAL_DIR / f"{args.split}.jsonl")
    vectors = embed_in_batches(openai_embedder_from_settings(), [q.question for q in questions]).vectors
    votes = nearest(questions, vectors)
    client = openai_client_from_settings()
    limit = ModelCallLimit(settings.model_max_concurrent_calls, settings.model_slot_wait_seconds)
    router = QuestionRouter(OpenAIStructuredModel(client, settings.router_model, limit, settings.router_timeout_seconds,
                                                  routing.MAX_OUTPUT_TOKENS),
                            min_confidence=0.0, timeout_seconds=settings.router_timeout_seconds)
    started = time.perf_counter()
    records = []
    try:
        for number, (q, vote) in enumerate(zip(questions, votes), 1):
            records.append({"id": q.id, "question": q.question, "route_needed": q.route, "tags": list(q.tags),
                            "group": q.group, "keyword": routing.keyword_route(q.question),
                            "nearest": {"route": vote.route, "agreement": vote.agreement,
                                        "neighbours": list(vote.neighbours)},
                            "model": await ask_model(router, q)})
            print(f"\r{number}/{len(questions)}", end="", flush=True)
        print()
    finally:
        await client.close()
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    stem = RESULTS_DIR / f"{args.split}-route-{args.name}"
    with stem.with_suffix(".jsonl").open("w", encoding="utf-8", newline="\n") as f:
        for record in records:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    stem.with_suffix(".md").write_text(
        summarize(records, args.split, args.name, args.threshold, time.perf_counter() - started),
        encoding="utf-8", newline="\n")
    print(f"written: {stem}.jsonl, {stem.name}.md")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--split", choices=("dev", "test"), default="dev")
    parser.add_argument("--name", required=True, help="names the result files: eval/results/<split>-route-<name>.*")
    parser.add_argument("--threshold", type=float, default=0.7, help="the model's threshold shown next to the others")
    choose_event_loop()
    asyncio.run(main_async(parser.parse_args()))


if __name__ == "__main__":
    main()
