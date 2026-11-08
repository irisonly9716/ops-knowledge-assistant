"""One Text-to-SQL evaluation run: every question of a split with a gold SQL, through the baseline agent or the fixed
pipeline of the query path, scored on the rows of the last query that ran.

    uv run python -m evaluation.sql_run --split dev --name zero-shot-agent
    uv run python -m evaluation.sql_run --split dev --name pipeline --pipeline --dictionary --views --examples 3 --retries 2

The pipeline's switches add one thing at a time, so a comparison can measure each: --dictionary, --views, --examples and
--retries all default to off, and the last command is what the query path runs (app/config.py). Without --views the
pipeline reads the tables instead, each filtered to the asker's department the same way as the views.

Needs Postgres with the mock data (scripts.generate_mock_data) and migration V9, and OPENAI_API_KEY in .env.
Generated queries run on the query path's read-only connections, one SELECT at a time with 5 s each; nothing here is
reachable from /query.

Scored (evaluation.metrics):
  - execution accuracy: the last query's rows hold the gold result's numbers, row for row. The strict reading, all
    of a gold row's values with its names and dates, is reported next to it.
  - questions on both documents and data: their gold SQL is one choice of the numbers that support an answer, so
    they are reported apart, with the share of the gold's numbers found as well.
  - for staff and managers: with the agent, how many final queries don't name the asker's department; with the
    pipeline, how many ran with the department filter the code adds.

Questions go one at a time, so latencies are those of one question. Follow-ups are asked without the turns before
them: there is no conversation memory yet. A pipeline question has the query path's time budget (query_budget_seconds);
an agent's has QUESTION_TIMEOUT_SECONDS.

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
from app.rag.query_models import (ModelBusy, ModelCallLimit, OpenAIAnswerModel, OpenAIQueryEmbedder,
                                  openai_client_from_settings)
from app.server import choose_event_loop
from app.sql_agent import guard, pipeline
from app.sql_agent.pipeline import SqlOptions, SqlService
from app.time_budget import TimeBudget
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
INVALID = "invalid"   # the pipeline's explanation failed validation
CELL_CHARS = 160
# For the pipeline without --views: the tables, each read as a subquery holding one department's rows, as the views are
# (guard.checked). Never on the query path, which reads only the views. No ip_address, as in the views
_IN_DEPARTMENT = f"JOIN departments d ON d.department_id = c.department_id WHERE d.name = {guard.DEPARTMENT}"
TABLES_IN_ONE_DEPARTMENT = {
    "departments": f"SELECT * FROM departments WHERE name = {guard.DEPARTMENT}",
    "hosts": f"SELECT c.* FROM hosts c {_IN_DEPARTMENT}",
    "campaigns": f"SELECT c.* FROM campaigns c {_IN_DEPARTMENT}",
    "campaign_metrics": f"SELECT m.* FROM campaign_metrics m JOIN campaigns c USING (campaign_id) {_IN_DEPARTMENT}",
    "campaign_products": f"SELECT p.* FROM campaign_products p JOIN campaigns c USING (campaign_id) {_IN_DEPARTMENT}",
    "campaign_customer_behavior": (
        "SELECT b.id, b.campaign_id, b.customer_ref, b.region, b.dwell_time_minutes, b.products_purchased, "
        f"b.purchase_amount FROM campaign_customer_behavior b JOIN campaigns c USING (campaign_id) {_IN_DEPARTMENT}"),
}


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
        "cost_usd": str(price(settings.chat_model, run.input_tokens, run.output_tokens)),
        "total_ms": (time.perf_counter() - started) * 1000,
    }
    return record


async def ask_pipeline(service: SqlService, question: Question, *, budget_seconds: float | None = None) -> dict:
    """One question through the pipeline, with the time budget a query has on the query path."""
    budget_seconds = budget_seconds or settings.query_budget_seconds
    record = {"id": question.id, "question": question.question, "asker": question.asker.model_dump(),
              "route": question.route, "tags": list(question.tags), "gold_sql": question.gold_sql,
              "gold_result": question.gold_result}
    started = time.perf_counter()
    traced, status, error = None, None, None
    try:
        async with asyncio.timeout(budget_seconds):
            traced = await service.trace(question.question, role=question.asker.role,
                                         department=question.asker.department, today=AS_OF,
                                         budget=TimeBudget(budget_seconds))
    except TimeoutError:
        status = TIMEOUT
    except (APIError, ModelBusy, psycopg.Error) as e:
        status, error = ERROR, f"{type(e).__name__}: {e}"[:300]
    if traced is not None:
        status, error = (INVALID, str(traced.error)) if traced.error else (traced.answer.status, None)
    answered = status == pipeline.ANSWERED
    final = traced.answer.result if answered else None
    predicted = final.rows if final else ()
    limited = question.asker.role in ("staff", "manager")
    attempts = traced.attempts if traced else ()
    queries = [{"sql": a.ran or a.written, "written": a.written, "generate_ms": round(a.generate_ms),
                "ms": round(a.execute_ms), "error": a.error, "columns": None, "rows": None, "truncated": None}
               for a in attempts]
    if final:
        queries[-1] |= {"columns": list(final.columns), "rows": [list(r) for r in final.rows],
                        "truncated": final.truncated}
    writer = traced.writer_tokens if traced else (0, 0)
    explainer = traced.explainer_tokens if traced else (0, 0)
    record |= {
        "status": status, "error": error,
        "reply": traced.answer.text if traced and traced.answer else None,
        "examples": [e.question for e in traced.examples] if traced else [],
        "queries": queries,
        "final_sql": traced.answer.sql if answered else None,
        "exact": answered and metrics.rows_match(question.gold_result, predicted),
        "exact_every_value": answered and metrics.rows_match(question.gold_result, predicted, every_value=True),
        "value_recall": metrics.value_recall(question.gold_result, predicted) if answered else 0.0,
        # Read from what ran: that the code's filter was there, whatever the model wrote
        "department_filtered": (guard.DEPARTMENT in traced.answer.sql.lower() if answered and limited else None),
        "model_calls": len(attempts) + (1 if explainer != (0, 0) else 0),
        "input_tokens": writer[0] + explainer[0], "output_tokens": writer[1] + explainer[1],
        "writer_tokens": list(writer), "explainer_tokens": list(explainer),
        "cost_usd": str(price(settings.sql_model, *writer) + price(settings.chat_model, *explainer)),
        "embed_ms": traced.embed_ms if traced else None, "context_ms": traced.context_ms if traced else None,
        "explain_ms": traced.explain_ms if traced else None,
        "total_ms": (time.perf_counter() - started) * 1000,
    }
    return record


def summarize(records: list[dict], name: str, split: str, setup: str, instructions: str, run_seconds: float) -> str:
    """setup: what answered the questions, in a sentence or two."""
    statuses = Counter(r["status"] for r in records)
    lines = [f"# Text-to-SQL evaluation run: {split}-sql-{name}", "",
             f"{len(records)} questions with a gold SQL from `eval/{split}.jsonl`. {setup} Statuses: "
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

    lines += ["", "## The asker's department", ""]
    limited = [r for r in records if r.get("names_department") is not None]
    if limited:
        unnamed = [r["id"] for r in limited if not r["names_department"]]
        lines.append(f"Final queries for staff and managers that don't name the asker's department: {len(unnamed)} of "
                     f"{len(limited)}" + (f" ({', '.join(unnamed)})" if unnamed else "") + ". Read from the text of "
                     "the query; the filter that counts is the one code adds and row-level security enforces.")
    filtered = [r for r in records if r.get("department_filtered") is not None]
    if filtered:
        missing = [r["id"] for r in filtered if not r["department_filtered"]]
        lines.append(f"Answered queries for staff and managers that ran with the department filter the code adds: "
                     f"{len(filtered) - len(missing)} of {len(filtered)}"
                     + (f" (without: {', '.join(missing)})" if missing else "") + ".")
    lines.append("")

    if any("writer_tokens" in r for r in records):
        lines += attempts_section(records)

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
    cost = sum((Decimal(r["cost_usd"]) for r in records), Decimal(0))
    lines += ["## Latency and cost", "", "| | p50 | p95 | max |", "|---|---|---|---|"]
    if totals:
        lines.append(f"| ms a question | {metrics.percentile(totals, 50):.0f} | {metrics.percentile(totals, 95):.0f} | "
                     f"{max(totals):.0f} |")
        stages = [r for r in ran if r.get("embed_ms") is not None]
        for label, values in (("embedding, ms", [r["embed_ms"] for r in stages]),
                              ("schema, names and examples, ms", [r["context_ms"] for r in stages]),
                              ("writing queries, ms", [sum(q["generate_ms"] for q in r["queries"]) for r in stages]),
                              ("running queries, ms", [sum(q["ms"] for q in r["queries"]) for r in stages]),
                              ("explaining, ms", [r["explain_ms"] for r in stages])):
            if values:
                lines.append(f"| {label} | {metrics.percentile(values, 50):.0f} | "
                             f"{metrics.percentile(values, 95):.0f} | {max(values):.0f} |")
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


def attempts_section(records: list[dict]) -> list[str]:
    """The pipeline: how many tries a question took, and what was wrong with the queries that failed."""
    answered = [r for r in records if r["status"] == ANSWERED]
    by_tries = Counter(len(r["queries"]) for r in answered)
    corrected = [r for r in answered if len(r["queries"]) > 1]
    failures = [q["error"] for r in records for q in r["queries"] if q["error"]]
    kinds = Counter(error.split(":", 1)[0] for error in failures)
    lines = ["## Writing and correcting", "",
             "Answered on the first query: " + str(by_tries.get(1, 0)) + "; after correcting: "
             + (", ".join(f"{n} after {tries - 1} {'retry' if tries == 2 else 'retries'}"
                          for tries, n in sorted(by_tries.items()) if tries > 1) or "none")
             + f". Of the corrected ones, {sum(r['exact'] for r in corrected)} of {len(corrected)} were right.", "",
             f"Queries that failed: {len(failures)}" + (" (" + ", ".join(f"{k} {v}" for k, v in kinds.most_common())
                                                       + ")" if failures else "") + "."]
    lines += [f"- `{r['id']}`: " + "; ".join(q["error"][:160].replace("|", "/").replace("\n", " ")
                                             for q in r["queries"] if q["error"])
              for r in records if any(q["error"] for q in r["queries"])]
    return lines + [""]


def pipeline_service(client, pool, args: argparse.Namespace) -> SqlService:
    limit = ModelCallLimit(settings.model_max_concurrent_calls, settings.model_slot_wait_seconds)
    embedder = OpenAIQueryEmbedder(client, settings.embedding_model, limit, settings.query_embedding_timeout_seconds)
    writer = OpenAIAnswerModel(client, settings.sql_model, limit, settings.query_model_timeout_seconds,
                               settings.answer_max_output_tokens)
    explainer = OpenAIAnswerModel(client, settings.chat_model, limit, settings.query_model_timeout_seconds,
                                  settings.answer_max_output_tokens)
    options = SqlOptions(examples=args.examples, retries=args.retries, dictionary=args.dictionary,
                         relations=guard.METRIC_VIEWS if args.views else TABLES_IN_ONE_DEPARTMENT)
    return SqlService(embedder if args.examples else None, writer, explainer, pool, options)


async def main_async(args: argparse.Namespace) -> None:
    db.require_compatible_event_loop()
    if not settings.openai_api_key:
        raise SystemExit("OPENAI_API_KEY is not set: add it to .env at the repository root")
    questions = [q for q in load(EVAL_DIR / f"{args.split}.jsonl") if scored(q)]
    if args.limit:
        questions = metrics.sample(questions, args.limit, f"limit-{args.limit}", key=lambda q: q.id)
    with db.connect() as conn:
        department_ids = dict(conn.execute("SELECT name, department_id FROM departments").fetchall())
    mode = " + ".join(["zero-shot"] + (["data dictionary"] if args.dictionary else []) + (["views"] if args.views else [])
                      + ([f"{args.examples} examples"] if args.pipeline and args.examples else [])
                      + ([f"{args.retries} retries"] if args.pipeline and args.retries else []))
    if args.pipeline:
        setup = (f"Fixed pipeline ({mode}), writing with {settings.sql_model} and explaining with "
                 f"{settings.chat_model}, {settings.query_budget_seconds:g} s a question.")
        instructions = pipeline.instructions("staff", "Beauty", AS_OF)
    else:
        setup = (f"LangChain agent ({mode}), {settings.chat_model}, at most {sql_agent.MAX_MODEL_CALLS} model calls "
                 f"and {QUESTION_TIMEOUT_SECONDS:.0f} s a question.")
        instructions = sql_agent.instructions("staff", "Beauty", AS_OF)

    started = time.perf_counter()
    pool = db.query_pool()
    await pool.open()
    client = openai_client_from_settings() if args.pipeline else None
    records = []
    try:
        if args.pipeline:
            service = pipeline_service(client, pool, args)
            ask = lambda question: ask_pipeline(service, question)
        else:
            model = ChatOpenAI(model=settings.chat_model, api_key=settings.openai_api_key,
                               base_url=settings.openai_base_url, timeout=sql_agent.MODEL_TIMEOUT_SECONDS, max_retries=0)
            ask = lambda question: ask_one(model, pool, question, department_ids, dictionary=args.dictionary,
                                           views=args.views)
        for number, question in enumerate(questions, 1):
            records.append(await ask(question))
            print(f"\r{number}/{len(questions)}", end="", flush=True)
        print()
    finally:
        await pool.close()
        if client:
            await client.close()

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    stem = RESULTS_DIR / f"{args.split}-sql-{args.name}"
    with stem.with_suffix(".jsonl").open("w", encoding="utf-8", newline="\n") as out:
        for record in records:
            out.write(json.dumps(record, ensure_ascii=False) + "\n")
    report = summarize(records, args.name, args.split, setup, instructions, time.perf_counter() - started)
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
    parser.add_argument("--pipeline", action="store_true", help="the fixed pipeline of the query path, not the agent")
    parser.add_argument("--examples", type=int, default=0, help="pipeline: example questions shown with each question")
    parser.add_argument("--retries", type=int, default=0, help="pipeline: queries written again after one fails")
    args = parser.parse_args()
    if not args.pipeline and (args.examples or args.retries):
        parser.error("--examples and --retries are for the pipeline")
    choose_event_loop()
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
