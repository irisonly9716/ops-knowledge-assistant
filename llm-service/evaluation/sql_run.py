"""One Text-to-SQL evaluation run: every question of a split with a gold SQL, through the baseline agent, scored on the
rows its last query returned.

    uv run python -m evaluation.sql_run --split dev --name zero-shot-agent

Needs Postgres with the mock data (scripts.generate_mock_data) and migration V9, and OPENAI_API_KEY in .env.
Generated queries run on the query path's read-only connections, one SELECT at a time with 5 s each; nothing here is
reachable from /query.

Scored (evaluation.metrics):
  - execution accuracy: the last query's rows hold the gold result's numbers, row for row. The strict reading, all
    of a gold row's values with its names and dates, is reported next to it.
  - questions on both documents and data: their gold SQL is one choice of the numbers that support an answer, so
    they are reported apart, with the share of the gold's numbers found as well.
  - for staff and managers, how many final queries don't name the asker's department.

Questions go one at a time, so latencies are those of one question. Follow-ups are asked without the turns before
them: there is no conversation memory yet.

Writes eval/results/<split>-sql-<name>.jsonl (one record per question) and .md (the tables).
"""
import argparse
import asyncio
import json
import time
from collections import Counter
from decimal import Decimal

import psycopg
from langchain_openai import ChatOpenAI
from openai import APIError

from app import db
from app.config import settings
from app.server import choose_event_loop
from evaluation import metrics, sql_agent
from evaluation.dataset import Question, load
from evaluation.run import EVAL_DIR, RESULTS_DIR, pct, price
from scripts.generate_mock_data import AS_OF

# 8 model calls of up to 30 s and their queries could take over 4 minutes; a question that takes 2 is counted as
# failed, which is what a user would see long before that
QUESTION_TIMEOUT_SECONDS = 120.0
TAGS = ("direct", "paraphrase", "colloquial", "vague", "keyword", "single_table", "multi_table", "time_range",
        "planning_advice", "multi_turn")
ANSWERED, NO_QUERY, STOPPED, TIMEOUT, ERROR = "answered", "no query", "stopped", "timeout", "error"
CELL_CHARS = 160


def cell(row) -> str:
    """A row for a table cell: a query can return whole JSON documents, which would fill the page."""
    text = "–" if row is None else ", ".join("NULL" if v is None else str(v) for v in row)
    text = text.replace("|", "/")
    return text if len(text) <= CELL_CHARS else text[:CELL_CHARS] + " …"


def scored(question: Question) -> bool:
    return question.gold_sql is not None and question.gold_result is not None


async def ask_one(model, pool, question: Question, department_ids: dict[str, int], *, dictionary: bool,
                  views: bool) -> dict:
    record = {"id": question.id, "question": question.question, "asker": question.asker.model_dump(),
              "route": question.route, "tags": list(question.tags), "gold_sql": question.gold_sql,
              "gold_result": question.gold_result}
    run = sql_agent.AgentRun()
    started = time.perf_counter()
    status, error = None, None
    try:
        async with asyncio.timeout(QUESTION_TIMEOUT_SECONDS):
            await sql_agent.ask(model, pool, question.question, role=question.asker.role,
                                department=question.asker.department, today=AS_OF, dictionary=dictionary,
                                views=views, run=run)
    except TimeoutError:
        status = TIMEOUT
    except (APIError, psycopg.Error) as e:
        status, error = ERROR, f"{type(e).__name__}: {e}"[:300]
    if status is None:
        status = STOPPED if run.stopped else (ANSWERED if run.final else NO_QUERY)
    # Only a run that ended with a reply is scored on its last query: a user sees nothing of one that was stopped
    final = run.final if status == ANSWERED else None
    predicted = final.result.rows if final else ()
    department = question.asker.department
    limited = question.asker.role in ("staff", "manager")
    record |= {
        "status": status, "error": error, "reply": run.reply,
        "queries": [{"sql": q.sql, "ms": round(q.ms), "error": q.error,
                     "columns": list(q.result.columns) if q.result else None,
                     "rows": [list(r) for r in q.result.rows] if q.result else None,
                     "truncated": q.result.truncated if q.result else None} for q in run.queries],
        "final_sql": final.sql if final else None,
        "exact": bool(final) and metrics.rows_match(question.gold_result, predicted),
        "exact_every_value": bool(final) and metrics.rows_match(question.gold_result, predicted, every_value=True),
        "value_recall": metrics.value_recall(question.gold_result, predicted) if final else 0.0,
        "names_department": (metrics.mentions_department(final.sql, department, department_ids.get(department))
                             if final and limited else None),
        "model_calls": run.model_calls, "input_tokens": run.input_tokens, "output_tokens": run.output_tokens,
        "total_ms": (time.perf_counter() - started) * 1000,
    }
    return record


def summarize(records: list[dict], name: str, split: str, mode: str, instructions: str, run_seconds: float) -> str:
    statuses = Counter(r["status"] for r in records)
    lines = [f"# Text-to-SQL evaluation run: {split}-sql-{name}", "",
             f"{len(records)} questions with a gold SQL from `eval/{split}.jsonl`. LangChain agent ({mode}), "
             f"{settings.chat_model}, at most {sql_agent.MAX_MODEL_CALLS} model calls and "
             f"{QUESTION_TIMEOUT_SECONDS:.0f} s a question. Statuses: "
             + ", ".join(f"{k} {v}" for k, v in statuses.most_common()) + f". {run_seconds:.0f} s in all.", "",
             "## Execution accuracy", "",
             "The last query's rows against the gold result: same number of rows, and each holds its gold row's "
             "numbers (within 0.5%), or all its values when it has no numbers. Strict: all of the gold row's values, "
             "names and dates included.", "",
             "| Questions | n | Execution accuracy | Strict | Gold numbers found |", "|---|---|---|---|---|"]

    def row(label: str, group: list[dict]) -> None:
        if group:
            recall = metrics.mean(r["value_recall"] for r in group if r["value_recall"] is not None)
            lines.append(f"| {label} | {len(group)} | {pct(metrics.mean(float(r['exact']) for r in group))} | "
                         f"{pct(metrics.mean(float(r['exact_every_value']) for r in group))} | {pct(recall)} |")

    data = [r for r in records if r["route"] == "data"]
    both = [r for r in records if r["route"] == "both"]
    row("**data: the number is the answer**", data)
    row("both: numbers that support an answer", both)
    for tag in TAGS:
        row(f"data, {tag}", [r for r in data if tag in r["tags"]])
    for role in ("staff", "manager", "executive"):
        row(f"data, asked by {role}", [r for r in data if r["asker"]["role"] == role])

    limited = [r for r in records if r["names_department"] is not None]
    unnamed = [r["id"] for r in limited if not r["names_department"]]
    lines += ["", "## The asker's department", "",
              f"Final queries for staff and managers that don't name the asker's department: {len(unnamed)} of "
              f"{len(limited)}" + (f" ({', '.join(unnamed)})" if unnamed else "") + ". Read from the text of the "
              "query; the filter that counts is the one code adds and row-level security enforces.", ""]

    lines += ["## Wrong", "", "| Question | Status | Gold, first row | Last query, first row |", "|---|---|---|---|"]
    for r in records:
        if not r["exact"]:
            final = next((q for q in reversed(r["queries"]) if q["rows"] is not None), None)
            got = final["rows"][0] if final and final["rows"] else None
            rows = (f"{len(final['rows'])} row{'' if len(final['rows']) == 1 else 's'}" if final else "no rows")
            lines.append(f"| `{r['id']}` {r['question']} | {r['status']} | {cell(r['gold_result'][0])} | "
                         f"{cell(got)} ({rows}) |")
    lines.append("")

    ran = [r for r in records if r["status"] != ERROR]
    totals = [r["total_ms"] for r in ran]
    cost = sum((price(settings.chat_model, r["input_tokens"], r["output_tokens"]) for r in records), Decimal(0))
    lines += ["## Latency and cost", "", "| | p50 | p95 | max |", "|---|---|---|---|"]
    if totals:
        lines.append(f"| ms a question | {metrics.percentile(totals, 50):.0f} | {metrics.percentile(totals, 95):.0f} | "
                     f"{max(totals):.0f} |")
        calls = [r["model_calls"] for r in ran]
        queries = [len(r["queries"]) for r in ran]
        lines.append(f"| model calls | {metrics.percentile(calls, 50)} | {metrics.percentile(calls, 95)} | {max(calls)} |")
        lines.append(f"| queries run | {metrics.percentile(queries, 50)} | {metrics.percentile(queries, 95)} | "
                     f"{max(queries)} |")
    if records:
        lines += ["", f"${cost:.4f} in all, ${cost / len(records):.5f} a question; input "
                  f"{metrics.mean(r['input_tokens'] for r in records):.0f} and output "
                  f"{metrics.mean(r['output_tokens'] for r in records):.0f} tokens a question on average."]
    lines += ["", "## Instructions", "", "As for a staff member in Beauty:", "", "```", instructions, "```", ""]
    return "\n".join(lines)


async def main_async(args: argparse.Namespace) -> None:
    db.require_compatible_event_loop()
    if not settings.openai_api_key:
        raise SystemExit("OPENAI_API_KEY is not set: add it to .env at the repository root")
    questions = [q for q in load(EVAL_DIR / f"{args.split}.jsonl") if scored(q)]
    if args.limit:
        questions = metrics.sample(questions, args.limit, f"limit-{args.limit}", key=lambda q: q.id)
    with db.connect() as conn:
        department_ids = dict(conn.execute("SELECT name, department_id FROM departments").fetchall())
    model = ChatOpenAI(model=settings.chat_model, api_key=settings.openai_api_key, base_url=settings.openai_base_url,
                       timeout=sql_agent.MODEL_TIMEOUT_SECONDS, max_retries=0)
    mode = " + ".join(["zero-shot"] + (["data dictionary"] if args.dictionary else []) + (["views"] if args.views else []))

    started = time.perf_counter()
    pool = db.query_pool()
    await pool.open()
    records = []
    try:
        for number, question in enumerate(questions, 1):
            records.append(await ask_one(model, pool, question, department_ids, dictionary=args.dictionary,
                                         views=args.views))
            print(f"\r{number}/{len(questions)}", end="", flush=True)
        print()
    finally:
        await pool.close()

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    stem = RESULTS_DIR / f"{args.split}-sql-{args.name}"
    with stem.with_suffix(".jsonl").open("w", encoding="utf-8", newline="\n") as out:
        for record in records:
            out.write(json.dumps(record, ensure_ascii=False) + "\n")
    report = summarize(records, args.name, args.split, mode, sql_agent.instructions("staff", "Beauty", AS_OF),
                       time.perf_counter() - started)
    stem.with_suffix(".md").write_text(report, encoding="utf-8", newline="\n")
    print(report)
    print(f"written: {stem}.jsonl, {stem.name}.md")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--split", choices=("dev", "test"), default="dev")
    parser.add_argument("--name", required=True, help="names the result files: eval/results/<split>-sql-<name>.*")
    parser.add_argument("--limit", type=int, help="only this many questions, picked at random: a quick check")
    parser.add_argument("--dictionary", action="store_true", help="show the data dictionary and the names in the data")
    parser.add_argument("--views", action="store_true", help="offer the metric views instead of the tables")
    args = parser.parse_args()
    choose_event_loop()
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
