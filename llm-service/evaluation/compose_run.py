"""The answer written from documents and data together, composed again from the same inputs at each reasoning effort,
or with other instructions, and graded.

    uv run python -m evaluation.compose_run --split dev --name 1 --from dev-flow-3 --efforts none,low --repeats 2
    uv run python -m evaluation.compose_run --split dev --name 2 --efforts none --instructions earlier.txt --file-repeats 1
    uv run python -m evaluation.compose_run --split dev --name 2 --efforts none --repeats 1 --against dev-compose-1

The questions are those whose answer a whole-path run (--from) wrote from both. Each goes through app/query_flow.py
once, to get what the answer is written from: the excerpts, the query and its rows. A question the route sends
elsewhere this time is left out and counted. Then only the composing call (app/query_flow.composed) runs again for each
variant: each effort with the code's instructions, --repeats times, and with --instructions, each effort with that
file's as well, --file-repeats times. The file holds instructions as compose_instructions writes them, with
{department_scope} where the department's rule goes. Every variant in turn for each question, so they differ in nothing
else and a slow hour of the API falls on all of them alike.

--against compares with a variant (--against-variant) of an earlier run instead, already graded, on the questions both
runs composed. That saves composing and grading it again, at a price: the two runs took their inputs separately, so a
query that returned other rows this time differs in more than the instructions.

Graded as evaluation.run grades the documents' answers (evaluation/judge.py), with the query's rows given to the judge
as one more excerpt, since an answer from both rests on them too:
  coverage       the question's answer points the answer says; a refusal or an answer that failed validation counts 0,
                 so an effort can't look better by refusing what it finds hard
  faithfulness   the answer's claims the excerpts and the rows support, over the answers that make claims

The rules, set before the runs:
  efforts        low replaces none only if its coverage is at least 5 points higher and more than none differs from
                 itself between repeats, its faithfulness isn't lower, and the composing call's p50 grows by at most 1 s
  instructions   the code's replace the file's if their coverage is higher by more than they differ from themselves
                 between repeats; a fall in faithfulness of more than 3 points sends them back to be reworded first

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
from pathlib import Path
from typing import Any

import psycopg
from openai import APIError, AsyncOpenAI

from app import db
from app.config import settings
from app.main import query_flow_from_settings
from app.query_flow import QueryFlow, compose_instructions, composed
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
MAX_FAITHFULNESS_DROP = 0.03   # what new instructions may lose in faithfulness before they are reworded
DEPARTMENT_SCOPE = "{department_scope}"


@dataclass(frozen=True)
class Inputs:
    """What one answer from both is written from, as the query path had it."""
    question: Question
    asked: str                       # the question the paths answered: rewritten when it was
    excerpts: list[RetrievedChunk]
    data: SqlTrace
    searched: list[str] | None
    ran: tuple[str, ...]


@dataclass(frozen=True)
class Variant:
    """One way of composing: an answer model at its effort, and instructions from a file or, with no template, the
    code's."""
    name: str
    effort: str
    composer: Any
    template: str | None = None
    repeats: int = 1

    def instructions(self, inputs: "Inputs") -> str | None:
        if self.template is None:
            return None
        return self.template.replace(DEPARTMENT_SCOPE, pipeline.department_scope(inputs.data.answer.department))


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


async def compose(variant: Variant, inputs: Inputs, repeat: int, budget_seconds: float) -> dict:
    record = {"id": inputs.question.id, "variant": variant.name, "effort": variant.effort, "repeat": repeat,
              "question": inputs.question.question, "asked": inputs.asked,
              "answer_points": list(inputs.question.answer_points),
              "excerpts": [{"content": c.content} for c in inputs.excerpts] + [{"content": rows_as_excerpt(inputs.data)}]}
    started = time.perf_counter()
    try:
        answer, error, tokens = await composed(variant.composer, inputs.asked, inputs.excerpts, inputs.data,
                                               inputs.searched, inputs.ran, TimeBudget(budget_seconds),
                                               variant.instructions(inputs))
    except (APIError, ModelBusy, TimeoutError) as failure:
        return record | {"status": ERROR, "error": f"{type(failure).__name__}: {failure}"[:300],
                         "ms": (time.perf_counter() - started) * 1000, "tokens": [0, 0]}
    record |= {"ms": (time.perf_counter() - started) * 1000, "tokens": list(tokens)}
    if error is not None:
        return record | {"status": INVALID, "error": str(error)[:300]}
    return record | {"status": answer.status, "answer": answer.text, "citations": len(answer.citations)}


async def compose_all(variants: list[Variant], taken: list[Inputs], budget_seconds: float) -> list[dict]:
    """One call at a time, so a composing call's time is its own; every variant in turn for each question, so a slow
    spell of the API falls on all of them alike. A variant with fewer repeats drops out after its last."""
    records = []
    for repeat in range(1, max(v.repeats for v in variants) + 1):
        for number, inputs in enumerate(taken, 1):
            for variant in variants:
                if repeat <= variant.repeats:
                    records.append(await compose(variant, inputs, repeat, budget_seconds))
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
    """Coverage and faithfulness of one variant, over all its repeats and for each."""
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
            "output_tokens": metrics.mean([r["tokens"][1] for r in records]) if records else None,
            "most_output_tokens": max((r["tokens"][1] for r in records), default=None)}


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


def spread(numbers: dict) -> float:
    by_repeat = numbers["coverage_by_repeat"]
    return max(by_repeat) - min(by_repeat) if len(by_repeat) > 1 and None not in by_repeat else 0.0


def instructions_verdict(code: dict, file: dict) -> tuple[bool, list[str]]:
    """The rule for instructions, applied: whether the code's replace the file's, and each condition as it came out."""
    gain, noise = code["coverage"] - file["coverage"], max(spread(code), spread(file))
    drop = file["faithfulness"] - code["faithfulness"]
    conditions = [
        (gain > noise, f"coverage {gain * 100:+.1f} points; the variants differ from themselves by up to "
                       f"{noise * 100:.1f} points between repeats"),
        (drop <= MAX_FAITHFULNESS_DROP, f"faithfulness {-drop * 100:+.1f} points, a fall of at most "
                                        f"{MAX_FAITHFULNESS_DROP * 100:.0f} allowed"),
    ]
    return all(ok for ok, _ in conditions), [f"{'met' if ok else 'not met'}: {text}" for ok, text in conditions]


def earlier_variant(records: list[dict], run: str, variant: str) -> list[dict]:
    """An earlier run's answers of one variant, named after the run; a run from before variants had only efforts."""
    return [r | {"variant": f"{variant}, {run}"} for r in records if r.get("variant", r.get("effort")) == variant]


def summarize(records: list[dict], skipped: dict[str, str], variants: list[str], split: str, name: str, source: str,
              judge_cost: Decimal, templates: dict[str, str] | None = None, earlier: list[dict] | None = None) -> str:
    """variants: their names, in order; templates: the instructions each file variant used; earlier: an earlier run's
    graded answers of one variant (earlier_variant), compared on the questions both runs composed."""
    numbers = {v: effort_numbers([r for r in records if r["variant"] == v]) for v in variants}
    compose_cost = sum((price(settings.chat_model, *r["tokens"]) for r in records), Decimal(0))
    questions = len({r["id"] for r in records})
    lines = [f"# Composing evaluation run: {split}-compose-{name}", "",
             f"{questions} questions whose answer `{source}` wrote from both, their inputs taken from one run through "
             f"the query path; {len(skipped)} left out because this run didn't write them from both"
             + (": " + ", ".join(f"`{k}` ({v})" for k, v in skipped.items()) if skipped else "") + ". "
             f"Each composed again as {', '.join(variants)}, graded by {JUDGE_MODEL}. A variant named after a file "
             "used that file's instructions; the others, the code's.", "",
             "| Variant | Answers | Answered / refused / invalid | Coverage, refusals as 0 | By repeat | Faithfulness | "
             "By repeat | Composing p50 / p95 | Output tokens, mean / most |", "|---|---|---|---|---|---|---|---|---|"]
    for variant, n in numbers.items():
        s = n["statuses"]
        lines.append(
            f"| {variant} | {n['n']} | {s[ANSWERED]} / {s['refused']} / {s[INVALID] + s[ERROR]} | {pct(n['coverage'])} | "
            f"{' / '.join(pct(v) for v in n['coverage_by_repeat'])} | {pct(n['faithfulness'])} | "
            f"{' / '.join(pct(v) for v in n['faithfulness_by_repeat'])} | "
            f"{n['p50_ms']:.0f} / {n['p95_ms']:.0f} ms | {n['output_tokens']:.0f} / {n['most_output_tokens']} |")
    if "none" in numbers and "low" in numbers and None not in (numbers["none"]["coverage"], numbers["low"]["coverage"]):
        replaces, conditions = verdict(numbers["none"], numbers["low"])
        lines += ["", f"**The rule: low {'replaces' if replaces else 'does not replace'} none.**", ""]
        lines += [f"- {c}" for c in conditions]
    for file_variant in (templates or {}):
        code_variant = file_variant.split(", ")[0]
        if code_variant in numbers and None not in (numbers[code_variant]["coverage"], numbers[file_variant]["coverage"]):
            kept, conditions = instructions_verdict(numbers[code_variant], numbers[file_variant])
            lines += ["", f"**The rule: the code's instructions {'replace' if kept else 'do not replace'} "
                          f"{file_variant.split(', ')[1]}'s at {code_variant}.**", ""]
            lines += [f"- {c}" for c in conditions]
    columns = list(variants)
    if earlier:
        then = earlier[0]["variant"]
        common = {r["id"] for r in records} & {r["id"] for r in earlier}
        code = then.split(", ")[0]
        now, before = (effort_numbers([r for r in group if r["id"] in common])
                       for group in ([r for r in records if r["variant"] == code], earlier))
        lines += ["", f"Against {then}, on the {len(common)} questions both runs composed:", "",
                  "| Variant | Answers | Coverage, refusals as 0 | By repeat | Faithfulness | Composing p50 / p95 | "
                  "Output tokens, mean / most |", "|---|---|---|---|---|---|---|"]
        for label, n in ((code, now), (then, before)):
            lines.append(f"| {label} | {n['n']} | {pct(n['coverage'])} | "
                         f"{' / '.join(pct(v) for v in n['coverage_by_repeat'])} | {pct(n['faithfulness'])} | "
                         f"{n['p50_ms']:.0f} / {n['p95_ms']:.0f} ms | {n['output_tokens']:.0f} / "
                         f"{n['most_output_tokens']} |")
        if None not in (now["coverage"], before["coverage"]):
            kept, conditions = instructions_verdict(now, before)
            lines += ["", f"**The rule: the code's instructions {'replace' if kept else 'do not replace'} {then}'s.**", ""]
            lines += [f"- {c}" for c in conditions]
        records = records + [r for r in earlier if r["id"] in common]
        columns.append(then)
    lines += ["", f"Cost: composing ${compose_cost:.4f}, grading ${judge_cost:.4f}.", "",
              "## Per question", "", "| Question | " + " | ".join(columns) + " |", "|---|" + "---|" * len(columns)]
    for qid in dict.fromkeys(r["id"] for r in records):
        cells = []
        for variant in columns:
            mine = [r for r in records if r["id"] == qid and r["variant"] == variant]
            cells.append(" / ".join(pct(coverage_counting_misses(r)) if r["status"] == ANSWERED else r["status"]
                                    for r in mine))
        question = next(r["question"] for r in records if r["id"] == qid).replace("|", "/")
        lines.append(f"| `{qid}` {question} | " + " | ".join(cells) + " |")
    lines += ["", "## Instructions", "", "The code's, for a department's asker:", "", "```",
              compose_instructions("<department>"), "```"]
    for variant, template in (templates or {}).items():
        lines += ["", f"{variant}:", "", "```", template, "```"]
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
    variants, templates = [], {}
    template = Path(args.instructions).read_text(encoding="utf-8") if args.instructions else None
    if template is not None and template.count(DEPARTMENT_SCOPE) != 1:
        raise SystemExit(f"{args.instructions} needs {DEPARTMENT_SCOPE} once, where the department's rule goes")
    for effort in efforts:
        composer = OpenAIAnswerModel(client, settings.chat_model, limit, settings.query_model_timeout_seconds,
                                     settings.compose_max_output_tokens, reasoning_effort=effort)
        variants.append(Variant(effort, effort, composer, None, args.repeats))
        if template is not None:
            name = f"{effort}, {Path(args.instructions).stem}"
            variants.append(Variant(name, effort, composer, template, args.file_repeats))
            templates[name] = template
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
        records = await compose_all(variants, taken, budget)
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
    earlier = None
    if args.against:
        with (RESULTS_DIR / f"{args.against}.jsonl").open(encoding="utf-8") as lines:
            earlier = earlier_variant([json.loads(line) for line in lines if line.strip()], args.against,
                                      args.against_variant)
    stem.with_suffix(".md").write_text(summarize(records, skipped, [v.name for v in variants], args.split, args.name,
                                                 args.source, judge_cost, templates, earlier),
                                       encoding="utf-8", newline="\n")
    print(f"written: {stem}.jsonl, {stem.name}.md")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--split", choices=("dev",), default="dev", help="tuning is done on dev only")
    parser.add_argument("--name", required=True, help="names the result files: eval/results/<split>-compose-<name>.*")
    parser.add_argument("--from", dest="source", default="dev-flow-3",
                        help="the whole-path run whose answers from both are composed again")
    parser.add_argument("--efforts", default="none,low", help="reasoning efforts to compare, comma-separated")
    parser.add_argument("--repeats", type=int, default=2, help="times each effort composes with the code's instructions")
    parser.add_argument("--instructions", help="a file of instructions to compose with as well, at each effort")
    parser.add_argument("--file-repeats", type=int, default=1, help="times each effort composes with the file's")
    parser.add_argument("--against", help="an earlier run to compare with, such as dev-compose-1, instead of composing "
                                          "its variant again")
    parser.add_argument("--against-variant", default="none", help="the earlier run's variant to compare with")
    choose_event_loop()
    asyncio.run(main_async(parser.parse_args()))


if __name__ == "__main__":
    main()
