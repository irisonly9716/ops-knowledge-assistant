"""Does reasoning help the answer written from documents and data together? The same inputs, composed again at each
reasoning effort, and graded.

    uv run python -m evaluation.compose_run --split dev --name 1 --from dev-flow-3 --efforts none,low --repeats 2

The questions are those whose answer a whole-path run (--from) wrote from both. Each goes through app/query_flow.py
once, to get what the answer is written from: the excerpts, the query and its rows. A question the route sends
elsewhere this time is left out and counted. Then only the composing call (app/query_flow.composed) runs again, at each
effort, --repeats times, every effort in turn for each question, so the efforts differ in nothing else and a slow hour
of the API falls on all of them alike.

Graded as evaluation.run grades the documents' answers (evaluation/judge.py), with the query's rows given to the judge
as one more excerpt, since an answer from both rests on them too:
  coverage       the question's answer points the answer says; a refusal or an answer that failed validation counts 0,
                 so an effort can't look better by refusing what it finds hard
  faithfulness   the answer's claims the excerpts and the rows support, over the answers that make claims

The rule, set before the run: low replaces none only if its coverage is at least 5 points higher and more than none
differs from itself between repeats, its faithfulness isn't lower, and the composing call's p50 grows by at most 1 s.

Needs Postgres with the corpus and the mock data, sql_reader enabled and OPENAI_API_KEY. Writes
eval/results/<split>-compose-<name>.jsonl and .md.
"""
import argparse
import asyncio
import json
import time
from collections import Counter
from dataclasses import dataclass
from decimal import Decimal

import psycopg
from openai import APIError, AsyncOpenAI

from app import db
from app.config import settings
from app.main import query_flow_from_settings
from app.query_flow import QueryFlow, composed
from app.rag.answering import ANSWERED, InvalidAnswer
from app.rag.query_models import ModelBusy, ModelCallLimit, OpenAIAnswerModel, openai_client_from_settings
from app.rag.retrieval import RetrievedChunk
from app.rag.reranking import tei_reranker_from_settings
from app.router.rewriting import Turn as RouterTurn
from app.server import choose_event_loop
from app.sql_agent import pipeline
from app.sql_agent.pipeline import SqlTrace
from app.time_budget import TimeBudget
from evaluation import metrics
from evaluation.dataset import Question, load
from evaluation.judge import OpenAIJudge
from evaluation.run import (EVAL_DIR, JUDGE_MODEL, JUDGE_RETRIES, JUDGE_TIMEOUT_SECONDS, RESULTS_DIR, grade_all, pct,
                            price)
from scripts.generate_mock_data import AS_OF

INVALID, ERROR = "invalid", "error"
MIN_GAIN = 0.05          # coverage points low must add
MAX_SLOWER_MS = 1000     # what low may add to the composing call's p50


@dataclass(frozen=True)
class Inputs:
    """What one answer from both is written from, as the query path had it."""
    question: Question
    asked: str                       # the question the paths answered: rewritten when it was
    excerpts: list[RetrievedChunk]
    data: SqlTrace
    searched: list[str] | None
    ran: tuple[str, ...]


def composed_ids(results: list[dict]) -> list[str]:
    return [r["id"] for r in results if r.get("written_by") == "both"]


async def inputs_of(flow: QueryFlow, question: Question, budget_seconds: float) -> Inputs | str:
    """The inputs, or why there are none this time."""
    try:
        traced = await flow.trace(question.question, role=question.asker.role, department=question.asker.department,
                                  today=AS_OF, budget=TimeBudget(budget_seconds),
                                  turns=tuple(RouterTurn(t.role, t.content) for t in question.turns))
    except (APIError, ModelBusy, psycopg.Error, TimeoutError) as error:
        return f"{type(error).__name__}"
    if traced.written_by != "both":
        return f"written by {traced.written_by} this time"
    return Inputs(question, traced.asked, traced.documents.excerpts, traced.data, traced.answer.searched_departments
                  if traced.answer else None, traced.answer.paths if traced.answer else ("documents", "data"))


def rows_as_excerpt(data: SqlTrace) -> str:
    return f"Result of a database query on the campaign data:\n{pipeline.show(data.answer.result)}"


async def compose(composer: OpenAIAnswerModel, inputs: Inputs, effort: str, repeat: int, budget_seconds: float) -> dict:
    record = {"id": inputs.question.id, "effort": effort, "repeat": repeat, "question": inputs.question.question,
              "asked": inputs.asked, "answer_points": list(inputs.question.answer_points),
              "excerpts": [{"content": c.content} for c in inputs.excerpts] + [{"content": rows_as_excerpt(inputs.data)}]}
    started = time.perf_counter()
    try:
        answer, error, tokens = await composed(composer, inputs.asked, inputs.excerpts, inputs.data, inputs.searched,
                                               inputs.ran, TimeBudget(budget_seconds))
    except (APIError, ModelBusy, TimeoutError) as failure:
        return record | {"status": ERROR, "error": f"{type(failure).__name__}: {failure}"[:300],
                         "ms": (time.perf_counter() - started) * 1000, "tokens": [0, 0]}
    record |= {"ms": (time.perf_counter() - started) * 1000, "tokens": list(tokens)}
    if error is not None:
        return record | {"status": INVALID, "error": str(error)[:300]}
    return record | {"status": answer.status, "answer": answer.text, "citations": len(answer.citations)}


async def compose_all(composers: dict[str, OpenAIAnswerModel], taken: list[Inputs], repeats: int,
                      budget_seconds: float) -> list[dict]:
    """One call at a time, so a composing call's time is its own; every effort in turn for each question, so a slow
    spell of the API falls on all of them alike."""
    records = []
    for repeat in range(1, repeats + 1):
        for number, inputs in enumerate(taken, 1):
            for effort, composer in composers.items():
                records.append(await compose(composer, inputs, effort, repeat, budget_seconds))
            print(f"\rrepeat {repeat}: {number}/{len(taken)}", end="", flush=True)
        print()
    return records


def coverage_counting_misses(record: dict) -> float | None:
    """A refusal or a failed answer says none of the points; a question without points has no coverage."""
    if not record["answer_points"]:
        return None
    if record["status"] != ANSWERED:
        return 0.0
    return record.get("coverage")   # None when the grading failed: left out


def effort_numbers(records: list[dict]) -> dict:
    """Coverage and faithfulness of one effort, over all its repeats and for each."""
    def coverage(group):
        values = [v for v in (coverage_counting_misses(r) for r in group) if v is not None]
        return metrics.mean(values) if values else None

    def faithfulness(group):
        values = [r["faithfulness"] for r in group if r.get("faithfulness") is not None]
        return metrics.mean(values) if values else None
    repeats = sorted({r["repeat"] for r in records})
    times = [r["ms"] for r in records if r["status"] != ERROR]
    return {"n": len(records), "statuses": Counter(r["status"] for r in records),
            "coverage": coverage(records), "faithfulness": faithfulness(records),
            "coverage_by_repeat": [coverage([r for r in records if r["repeat"] == k]) for k in repeats],
            "faithfulness_by_repeat": [faithfulness([r for r in records if r["repeat"] == k]) for k in repeats],
            "p50_ms": metrics.percentile(times, 50) if times else None,
            "p95_ms": metrics.percentile(times, 95) if times else None,
            "output_tokens": metrics.mean([r["tokens"][1] for r in records]) if records else None}


def verdict(none: dict, low: dict) -> tuple[bool, list[str]]:
    """The rule set before the run, applied: whether low replaces none, and each condition as it came out."""
    noise = (max(none["coverage_by_repeat"]) - min(none["coverage_by_repeat"])
             if len(none["coverage_by_repeat"]) > 1 and None not in none["coverage_by_repeat"] else 0.0)
    gain = low["coverage"] - none["coverage"]
    slower = low["p50_ms"] - none["p50_ms"]
    conditions = [
        (gain >= MIN_GAIN, f"coverage {gain * 100:+.1f} points, at least {MIN_GAIN * 100:.0f} needed"),
        (gain > noise, f"none differs from itself by {noise * 100:.1f} points between repeats"),
        (low["faithfulness"] >= none["faithfulness"],
         f"faithfulness {(low['faithfulness'] - none['faithfulness']) * 100:+.1f} points, not lower needed"),
        (slower <= MAX_SLOWER_MS, f"the composing call's p50 {slower:+.0f} ms, at most +{MAX_SLOWER_MS} ms allowed"),
    ]
    return all(ok for ok, _ in conditions), [f"{'met' if ok else 'not met'}: {text}" for ok, text in conditions]


def summarize(records: list[dict], skipped: dict[str, str], efforts: list[str], split: str, name: str, source: str,
              judge_cost: Decimal) -> str:
    numbers = {effort: effort_numbers([r for r in records if r["effort"] == effort]) for effort in efforts}
    compose_cost = sum((price(settings.chat_model, *r["tokens"]) for r in records), Decimal(0))
    questions = len({r["id"] for r in records})
    lines = [f"# Composing evaluation run: {split}-compose-{name}", "",
             f"{questions} questions whose answer `{source}` wrote from both, their inputs taken from one run through "
             f"the query path; {len(skipped)} left out because this run didn't write them from both"
             + (": " + ", ".join(f"`{k}` ({v})" for k, v in skipped.items()) if skipped else "") + ". "
             f"Each composed again at {', '.join(efforts)}, {max(r['repeat'] for r in records) if records else 0} "
             f"times, graded by {JUDGE_MODEL}.", "",
             "| Effort | Answers | Answered / refused / invalid | Coverage, refusals as 0 | By repeat | Faithfulness | "
             "By repeat | Composing p50 / p95 | Output tokens |", "|---|---|---|---|---|---|---|---|---|"]
    for effort, n in numbers.items():
        s = n["statuses"]
        lines.append(
            f"| {effort} | {n['n']} | {s[ANSWERED]} / {s['refused']} / {s[INVALID] + s[ERROR]} | {pct(n['coverage'])} | "
            f"{' / '.join(pct(v) for v in n['coverage_by_repeat'])} | {pct(n['faithfulness'])} | "
            f"{' / '.join(pct(v) for v in n['faithfulness_by_repeat'])} | "
            f"{n['p50_ms']:.0f} / {n['p95_ms']:.0f} ms | {n['output_tokens']:.0f} |")
    if "none" in numbers and "low" in numbers and None not in (numbers["none"]["coverage"], numbers["low"]["coverage"]):
        replaces, conditions = verdict(numbers["none"], numbers["low"])
        lines += ["", f"**The rule: low {'replaces' if replaces else 'does not replace'} none.**", ""]
        lines += [f"- {c}" for c in conditions]
    lines += ["", f"Cost: composing ${compose_cost:.4f}, grading ${judge_cost:.4f}.", "",
              "## Per question", "", "| Question | " + " | ".join(efforts) + " |", "|---|" + "---|" * len(efforts)]
    for qid in dict.fromkeys(r["id"] for r in records):
        cells = []
        for effort in efforts:
            mine = [r for r in records if r["id"] == qid and r["effort"] == effort]
            cells.append(" / ".join(pct(coverage_counting_misses(r)) if r["status"] == ANSWERED else r["status"]
                                    for r in mine))
        question = next(r["question"] for r in records if r["id"] == qid).replace("|", "/")
        lines.append(f"| `{qid}` {question} | " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"


async def main_async(args: argparse.Namespace) -> None:
    db.require_compatible_event_loop()
    if not settings.openai_api_key:
        raise SystemExit("OPENAI_API_KEY is not set: add it to .env at the repository root")
    efforts = args.efforts.split(",")
    questions = {q.id: q for q in load(EVAL_DIR / f"{args.split}.jsonl")}
    with (RESULTS_DIR / f"{args.source}.jsonl").open(encoding="utf-8") as lines:
        ids = composed_ids([json.loads(line) for line in lines if line.strip()])
    client = openai_client_from_settings()
    judge_client = AsyncOpenAI(api_key=settings.openai_api_key, base_url=settings.openai_base_url,
                               max_retries=JUDGE_RETRIES)
    pool, reader_pool = db.query_pool(), db.sql_pool()
    await pool.open()
    await reader_pool.open()
    reranker = tei_reranker_from_settings()
    budget = settings.query_budget_seconds
    limit = ModelCallLimit(settings.model_max_concurrent_calls, settings.model_slot_wait_seconds)
    composers = {effort: OpenAIAnswerModel(client, settings.chat_model, limit, settings.query_model_timeout_seconds,
                                           settings.answer_max_output_tokens, reasoning_effort=effort)
                 for effort in efforts}
    judge = OpenAIJudge(judge_client, JUDGE_MODEL, JUDGE_TIMEOUT_SECONDS)
    records, skipped = [], {}
    try:
        flow = query_flow_from_settings(client, pool, reader_pool, reranker)
        taken = []
        for number, qid in enumerate(ids, 1):
            found = await inputs_of(flow, questions[qid], budget)
            if isinstance(found, str):
                skipped[qid] = found
            else:
                taken.append(found)
            print(f"\rinputs {number}/{len(ids)}", end="", flush=True)
        print()
        records = await compose_all(composers, taken, args.repeats, budget)
        to_grade = [r for r in records if r["status"] == ANSWERED]
        print(f"grading {len(to_grade)} answers with {JUDGE_MODEL}")
        await grade_all(to_grade, judge)
    finally:
        await pool.close()
        await reader_pool.close()
        await client.close()
        await judge_client.close()
        if reranker:
            await reranker.aclose()
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    stem = RESULTS_DIR / f"{args.split}-compose-{args.name}"
    with stem.with_suffix(".jsonl").open("w", encoding="utf-8", newline="\n") as f:
        for record in records:
            f.write(json.dumps({k: v for k, v in record.items() if k != "excerpts"}, ensure_ascii=False) + "\n")
    judge_cost = price(judge.model, judge.input_tokens, judge.output_tokens)
    stem.with_suffix(".md").write_text(summarize(records, skipped, efforts, args.split, args.name, args.source,
                                                 judge_cost), encoding="utf-8", newline="\n")
    print(f"written: {stem}.jsonl, {stem.name}.md")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--split", choices=("dev",), default="dev", help="tuning is done on dev only")
    parser.add_argument("--name", required=True, help="names the result files: eval/results/<split>-compose-<name>.*")
    parser.add_argument("--from", dest="source", default="dev-flow-3",
                        help="the whole-path run whose answers from both are composed again")
    parser.add_argument("--efforts", default="none,low", help="reasoning efforts to compare, comma-separated")
    parser.add_argument("--repeats", type=int, default=2)
    choose_event_loop()
    asyncio.run(main_async(parser.parse_args()))


if __name__ == "__main__":
    main()
