"""The whole query path on a split's questions, as /query runs it: the route, both paths started at once, the one the
route rules out cancelled, and one answer (app/query_flow.py).

    uv run python -m evaluation.flow_run --split dev --name 1

What it measures: where each question went against where its answer is (a path missing, a path extra, as in
evaluation/route_run.py), how long each part took, what the model calls cost, and for a question with a gold SQL
whether the rows the answer rests on hold the gold result. Answers are not graded here: grading is most of what a run of
evaluation.run costs, and that run grades the documents path's answers.

Questions are asked one at a time, so the latency is one question's own. Follow-ups are asked without the turns before
them: the query path has no conversation memory yet. A cancelled model call's tokens are unknown, so the cost counts
only the calls that finished. Writes eval/results/<split>-flow-<name>.jsonl and .md.
"""
import argparse
import asyncio
import json
import time
from collections import Counter
from decimal import Decimal

import psycopg
from openai import APIError

from app import db
from app.config import settings
from app.main import query_flow_from_settings
from app.query_flow import QueryFlow
from app.rag.query_models import ModelBusy, openai_client_from_settings
from app.rag.reranking import tei_reranker_from_settings
from app.router import routing
from app.router.routing import DATA, DOCUMENTS
from app.server import choose_event_loop
from app.time_budget import TimeBudget
from evaluation import metrics
from evaluation.dataset import Question, load
from evaluation.route_run import scored
from evaluation.run import EVAL_DIR, RESULTS_DIR, pct, price
from scripts.generate_mock_data import AS_OF

TARGET_MS = 3000
TIMEOUT, ERROR, INVALID = "timeout", "error", "invalid"


async def ask(flow: QueryFlow, question: Question, budget_seconds: float) -> dict:
    record = {"id": question.id, "question": question.question, "asker": question.asker.model_dump(),
              "route_needed": question.route, "expected": question.expected, "tags": list(question.tags)}
    started = time.perf_counter()
    try:
        async with asyncio.timeout(budget_seconds):
            traced = await flow.trace(question.question, role=question.asker.role,
                                      department=question.asker.department, today=AS_OF,
                                      budget=TimeBudget(budget_seconds))
    except TimeoutError:
        return record | {"status": TIMEOUT, "total_ms": (time.perf_counter() - started) * 1000}
    except (APIError, ModelBusy, psycopg.Error) as error:
        return record | {"status": ERROR, "error": f"{type(error).__name__}: {error}"[:300],
                         "total_ms": (time.perf_counter() - started) * 1000}

    routed, documents, data, answer = traced.routing, traced.documents, traced.data, traced.answer
    sql_answer = data.answer if data else None
    rows = sql_answer.result.rows if sql_answer and sql_answer.result else None
    missing, extra = scored(question.route, routed.paths)
    record |= {
        "route": routed.route, "confidence": routed.confidence, "decided_by": routed.decided_by,
        "paths": [p for p in (DOCUMENTS, DATA) if p in routed.paths], "missing_path": missing, "extra_path": extra,
        "cancelled": list(traced.cancelled), "written_by": traced.written_by,
        "status": answer.status if answer else INVALID, "error": str(traced.error) if traced.error else None,
        "answer": answer.text if answer else None, "citations": len(answer.citations) if answer else 0,
        "sql": sql_answer.sql if sql_answer else None, "rows": len(rows) if rows is not None else None,
        "sql_attempts": len(data.attempts) if data else None,
        "exact": (metrics.rows_match(question.gold_result, rows)
                  if question.gold_result is not None and rows is not None else None),
        "embed_ms": traced.embed_ms, "route_ms": traced.route_ms, "paths_ms": traced.paths_ms,
        "compose_ms": traced.compose_ms, "total_ms": traced.total_ms,
        "explain_ms": data.explain_ms if data else None,
        "tokens": {"router": [routed.input_tokens, routed.output_tokens],
                   "documents": [documents.input_tokens, documents.output_tokens] if documents else [0, 0],
                   "writer": list(data.writer_tokens) if data else [0, 0],
                   "explainer": list(data.explainer_tokens) if data else [0, 0],
                   "compose": list(traced.compose_tokens)},
    }
    record["cost_usd"] = str(cost(record["tokens"]))
    return record


def cost(tokens: dict) -> Decimal:
    """The query embedding is left out: about 15 tokens, $0.0000003 a question."""
    return (price(settings.router_model, *tokens["router"]) + price(settings.chat_model, *tokens["documents"])
            + price(settings.sql_model, *tokens["writer"]) + price(settings.chat_model, *tokens["explainer"])
            + price(settings.chat_model, *tokens["compose"]))


def ms(values: list[float]) -> str:
    """p50 / p95 / max in one table cell."""
    return (f"{metrics.percentile(values, 50):.0f} / {metrics.percentile(values, 95):.0f} / {max(values):.0f}"
            if values else "–")


def summarize(records: list[dict], split: str, name: str, run_seconds: float) -> str:
    ran = [r for r in records if "route" in r]
    statuses = Counter(r["status"] for r in records)
    lines = [f"# Query path evaluation run: {split}-flow-{name}", "",
             f"{len(records)} questions from `eval/{split}.jsonl`, through app/query_flow.py as /query runs it, one at a "
             f"time. Statuses: " + ", ".join(f"{k} {v}" for k, v in statuses.most_common())
             + f". {run_seconds:.0f} s in all.", "",
             "## Where the questions went", "",
             f"A path missing: {sum(r['missing_path'] for r in ran)}; a path extra: {sum(r['extra_path'] for r in ran)}; "
             f"decided by the keyword rule: {sum(r['decided_by'] == routing.KEYWORDS for r in ran)}. "
             f"A path cancelled by the route: {sum(bool(r['cancelled']) for r in ran)} questions "
             f"({', '.join(f'{k} {v}' for k, v in Counter(p for r in ran for p in r['cancelled']).most_common()) or 'none'}).",
             "", "| The answer came from | Questions |", "|---|---|"]
    lines += [f"| {k} | {v} |" for k, v in Counter(r["written_by"] for r in ran).most_common()]

    lines += ["", "## Latency", "",
              f"Milliseconds, p50 / p95 / max, by the paths that ran. The target is {TARGET_MS / 1000:g} s a query, "
              "without the backend. Route and paths are measured from the start, so they overlap.", "",
              "| Paths | n | Under the target | Total | Route | Paths done | Answer from both |",
              "|---|---|---|---|---|---|---|"]
    for label, group in (("documents", [r for r in ran if r["paths"] == [DOCUMENTS]]),
                         ("data", [r for r in ran if r["paths"] == [DATA]]),
                         ("both", [r for r in ran if r["paths"] == [DOCUMENTS, DATA]]), ("all", ran)):
        if group:
            under = sum(r["total_ms"] < TARGET_MS for r in group)
            composed = [r["compose_ms"] for r in group if r["written_by"] == "both"]
            lines.append(f"| {label} | {len(group)} | {under} ({pct(under / len(group))}) | "
                         f"{ms([r['total_ms'] for r in group])} | {ms([r['route_ms'] for r in group])} | "
                         f"{ms([r['paths_ms'] for r in group])} | {ms(composed)} |")
    slow = sorted((r for r in ran if r["total_ms"] >= TARGET_MS), key=lambda r: -r["total_ms"])
    if slow:
        lines += ["", f"Over the target ({len(slow)}): " + "; ".join(
            f"`{r['id']}` {r['total_ms']:.0f} ms ({'+'.join(r['paths'])}, written by {r['written_by']}, "
            f"{r['sql_attempts'] or 0} queries)" for r in slow[:15]) + "."]

    costs = [Decimal(r["cost_usd"]) for r in ran]
    lines += ["", "## Cost", "",
              f"${sum(costs):.4f} in all, ${sum(costs) / max(len(costs), 1):.6f} a question on average, the calls that "
              "finished only. By the part of the path:", "",
              "| Part | Input tokens | Output tokens |", "|---|---|---|"]
    for part in ("router", "documents", "writer", "explainer", "compose"):
        lines.append(f"| {part} | {sum(r['tokens'][part][0] for r in ran)} | {sum(r['tokens'][part][1] for r in ran)} |")
    thrown = [r for r in ran if r["written_by"] == "both"]
    lines += ["", f"The documents' answer written while routing and then not used, because the answer came from both: "
                  f"{len(thrown)} questions, {sum(r['tokens']['documents'][0] for r in thrown)} input and "
                  f"{sum(r['tokens']['documents'][1] for r in thrown)} output tokens."]

    gold = [r for r in ran if r["exact"] is not None]
    with_gold = [r for r in records if r["route_needed"] in ("data", "both") and r["expected"] == "answer"]
    lines += ["", "## The data answers", "",
              f"Questions that need the data and have a gold result: {len(with_gold)}. The data path ran and came back "
              f"with rows for {len(gold)}; their rows hold the gold result for {sum(r['exact'] for r in gold)}"
              f" ({pct(sum(r['exact'] for r in gold) / len(gold)) if gold else '–'}). Measured as in evaluation/sql_run.py: "
              "same number of rows, each holding its gold row's numbers.", ""]

    trouble = [r for r in records if r["status"] in (TIMEOUT, ERROR, INVALID) or r.get("missing_path")]
    if trouble:
        lines += ["## Questions to look at", "", "| Question | Needed | Paths | Status | Note |", "|---|---|---|---|---|"]
        lines += [f"| `{r['id']}` {r['question'].replace('|', '/')} | {r['route_needed']} | "
                  f"{'+'.join(r.get('paths', [])) or '–'} | {r['status']} | "
                  f"{(r.get('error') or ('a path missing' if r.get('missing_path') else '')).replace('|', '/')[:160]} |"
                  for r in trouble]
        lines.append("")
    return "\n".join(lines)


async def main_async(args: argparse.Namespace) -> None:
    db.require_compatible_event_loop()
    if not settings.openai_api_key:
        raise SystemExit("OPENAI_API_KEY is not set: add it to .env at the repository root")
    questions = load(EVAL_DIR / f"{args.split}.jsonl")
    if args.limit:
        questions = questions[:args.limit]
    client = openai_client_from_settings()
    pool, reader_pool = db.query_pool(), db.sql_pool()
    await pool.open()
    await reader_pool.open()
    reranker = tei_reranker_from_settings()
    flow = query_flow_from_settings(client, pool, reader_pool, reranker)
    started = time.perf_counter()
    records = []
    try:
        for number, question in enumerate(questions, 1):
            records.append(await ask(flow, question, settings.query_budget_seconds))
            print(f"\r{number}/{len(questions)}", end="", flush=True)
        print()
    finally:
        await pool.close()
        await reader_pool.close()
        await client.close()
        if reranker:
            await reranker.aclose()
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    stem = RESULTS_DIR / f"{args.split}-flow-{args.name}"
    with stem.with_suffix(".jsonl").open("w", encoding="utf-8", newline="\n") as f:
        for record in records:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    stem.with_suffix(".md").write_text(summarize(records, args.split, args.name, time.perf_counter() - started),
                                       encoding="utf-8", newline="\n")
    print(f"written: {stem}.jsonl, {stem.name}.md")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--split", choices=("dev", "test"), default="dev")
    parser.add_argument("--name", required=True, help="names the result files: eval/results/<split>-flow-<name>.*")
    parser.add_argument("--limit", type=int, default=0, help="only the first n questions, to try the run out")
    choose_event_loop()
    asyncio.run(main_async(parser.parse_args()))


if __name__ == "__main__":
    main()
