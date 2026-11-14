"""The rewrite alone on a split's questions: which it rewrites, into what, and which it leaves as asked.

    uv run python -m evaluation.rewrite_run --split dev --name 1                 # every question, about $0.009
    uv run python -m evaluation.rewrite_run --split dev --name 1 --only hard     # follow-ups and relative words
    uv run python -m evaluation.rewrite_run --split dev --name 1 --only hard --ids q-408a933928   # and these

Only the rewrite's model call (app/router/rewriting.py), as the query path makes it, with each follow-up's turns: no
search, no SQL, no answer, so trying its instructions costs a fraction of evaluation.flow_run. The questions carry no
label for the rewrite, so it is read by groups and by eye:
  - follow-ups: their turns are needed to understand them, so each should come back rewritten
  - questions with a word placing a campaign or a time relative to now ("last", "latest", "this year"): most should get
    a placeholder; "the previous batch" or "next time" shouldn't
  - the rest: should come back as asked
Writes eval/results/<split>-rewrite-<name>.jsonl and .md.
"""
import argparse
import asyncio
import json
import re
import time
from collections import Counter

from app.config import settings
from app.rag.query_models import ModelCallLimit, OpenAIStructuredModel, openai_client_from_settings
from app.router import rewriting
from app.router.rewriting import QuestionRewriter, Turn
from app.server import choose_event_loop
from app.time_budget import TimeBudget
from evaluation import metrics
from evaluation.dataset import Question, load
from evaluation.run import EVAL_DIR, RESULTS_DIR, price

FOLLOW_UP, RELATIVE, OTHER = "follow-up", "relative word", "other"
_RELATIVE = re.compile(r"\b(last|latest|most recent|previous|recent|before that|this (?:year|month|quarter)|"
                       r"last (?:year|month|quarter)|ago)\b", re.I)


def group(question: Question) -> str:
    if question.turns:
        return FOLLOW_UP
    return RELATIVE if _RELATIVE.search(question.question) else OTHER


async def ask(rewriter: QuestionRewriter, question: Question) -> dict:
    started = time.perf_counter()
    rewritten = await rewriter.rewrite(question.question, tuple(Turn(t.role, t.content) for t in question.turns),
                                       TimeBudget(settings.query_budget_seconds))
    return {"id": question.id, "question": question.question, "group": group(question),
            "turns": [f"{t.role}: {t.content}" for t in question.turns],
            "rewritten": rewritten.standalone, "reason": rewritten.reason,
            "campaign": rewritten.campaign.model_dump() if rewritten.campaign else None,
            "period": rewritten.period.model_dump() if rewritten.period else None,
            "tokens": [rewritten.input_tokens, rewritten.output_tokens], "ms": (time.perf_counter() - started) * 1000}


def summarize(records: list[dict], split: str, name: str) -> str:
    by_group = {g: [r for r in records if r["group"] == g] for g in (FOLLOW_UP, RELATIVE, OTHER)}
    times = [r["ms"] for r in records]
    cost = sum(price(settings.rewrite_model, *r["tokens"]) for r in records)
    lines = [f"# Rewrite evaluation run: {split}-rewrite-{name}", "",
             f"{len(records)} questions from `eval/{split}.jsonl`, the rewrite alone, with each follow-up's turns. "
             f"${cost:.4f}; p50 {metrics.percentile(times, 50):.0f} ms, p95 {metrics.percentile(times, 95):.0f} ms.", "",
             "| Group | Questions | Rewritten | With a campaign | With a period | Left as asked, by why |",
             "|---|---|---|---|---|---|"]
    for label, rows in by_group.items():
        if rows:
            reasons = Counter(r["reason"] for r in rows if not r["rewritten"])
            lines.append(f"| {label} | {len(rows)} | {sum(bool(r['rewritten']) for r in rows)} | "
                         f"{sum(bool(r['campaign']) for r in rows)} | {sum(bool(r['period']) for r in rows)} | "
                         + "; ".join(f"{k} {v}" for k, v in reasons.most_common()) + " |")
    for label, rows in by_group.items():
        shown = rows if label != OTHER else [r for r in rows if r["rewritten"]]
        if not shown:
            continue
        lines += ["", f"## {label}" + (": only those rewritten" if label == OTHER else ""), "",
                  "| Question | Rewritten | Reference |", "|---|---|---|"]
        for r in shown:
            reference = (f"campaign: {r['campaign']['words']}, {r['campaign']['department']}, "
                         f"{r['campaign']['position']}" if r["campaign"] else "")
            reference += (" " if reference and r["period"] else "") + (
                f"period: {r['period']['kind']} {r['period']['months'] or ''}".strip() if r["period"] else "")
            asked = r["question"].replace("|", "/")
            if r["turns"]:
                asked = " / ".join(t.replace("|", "/")[:80] for t in r["turns"]) + f" / **{asked}**"
            lines.append(f"| `{r['id']}` {asked} | {(r['rewritten'] or '– (' + r['reason'] + ')').replace('|', '/')} | "
                         f"{reference or '–'} |")
    lines.append("")
    return "\n".join(lines)


async def main_async(args: argparse.Namespace) -> None:
    if not settings.openai_api_key:
        raise SystemExit("OPENAI_API_KEY is not set: add it to .env at the repository root")
    questions = load(EVAL_DIR / f"{args.split}.jsonl")
    wanted = set(args.ids.split(",")) if args.ids else set()
    if args.only == "hard" or wanted:
        questions = [q for q in questions if (args.only == "hard" and group(q) != OTHER) or q.id in wanted]
    client = openai_client_from_settings()
    limit = ModelCallLimit(settings.model_max_concurrent_calls, settings.model_slot_wait_seconds)
    rewriter = QuestionRewriter(OpenAIStructuredModel(client, settings.rewrite_model, limit,
                                                      settings.rewrite_timeout_seconds, rewriting.MAX_OUTPUT_TOKENS),
                                timeout_seconds=settings.rewrite_timeout_seconds)
    records = []
    try:
        for number, question in enumerate(questions, 1):
            records.append(await ask(rewriter, question))
            print(f"\r{number}/{len(questions)}", end="", flush=True)
        print()
    finally:
        await client.close()
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    stem = RESULTS_DIR / f"{args.split}-rewrite-{args.name}"
    with stem.with_suffix(".jsonl").open("w", encoding="utf-8", newline="\n") as f:
        for record in records:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    stem.with_suffix(".md").write_text(summarize(records, args.split, args.name), encoding="utf-8", newline="\n")
    print(f"written: {stem}.jsonl, {stem.name}.md")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--split", choices=("dev", "test"), default="dev")
    parser.add_argument("--name", required=True, help="names the result files: eval/results/<split>-rewrite-<name>.*")
    parser.add_argument("--only", choices=("all", "hard"), default="all",
                        help="hard: only the follow-ups and the questions with a relative word")
    parser.add_argument("--ids", default="", help="these questions too, comma-separated: ones a run got wrong")
    choose_event_loop()
    asyncio.run(main_async(parser.parse_args()))


if __name__ == "__main__":
    main()
