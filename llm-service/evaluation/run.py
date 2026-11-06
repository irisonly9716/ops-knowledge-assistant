"""One evaluation run: every question of a split through the real query path, then the numbers.

    uv run python -m evaluation.run --split dev --name baseline

Needs Postgres with the corpus ingested (scripts.ingest_corpus) and OPENAI_API_KEY in .env. It calls the same
QueryService as /query, in this process instead of over HTTP, for two reasons: the search can return the top 50 to
score recall on, and the run can use a threshold of 0, so every question reaches the model and one run gives the whole
threshold sweep (evaluation.metrics). Latency is therefore llm-service's own, without the backend in front of it.

Questions go one at a time, so a latency is that of one query, not of several sharing the model. The grading calls,
which aren't part of a query, run a few at once.

What it can't measure yet:
  - questions about numbers: Text-to-SQL isn't built. Every question still goes through the path, and the ones on
    both documents and data are scored on retrieval, but none of those answers is graded.
  - follow-ups: there is no conversation memory yet, so a follow-up is asked without the turns before it.

Writes eval/results/<split>-<name>.jsonl (one record per question), .md (the tables) and -spot-check.md (answers
picked at random for a person to check the judge against).
"""
import argparse
import asyncio
import json
import time
from collections import Counter, defaultdict
from decimal import Decimal
from pathlib import Path

import psycopg
import tiktoken
from openai import APIError, AsyncOpenAI

from app import db
from app.config import settings
from app.rag.answering import INSTRUCTIONS, QueryService
from app.rag.chunking import TOKEN_ENCODING
from app.rag.query_models import ModelBusy, ModelCallLimit, OpenAIAnswerModel, OpenAIQueryEmbedder, openai_client_from_settings
from app.server import choose_event_loop
from app.time_budget import TimeBudget
from evaluation import metrics
from evaluation.dataset import Question, Section, load, resolve
from evaluation.judge import JudgeError, OpenAIJudge, grade

REPO_ROOT = Path(__file__).resolve().parents[2]
EVAL_DIR = REPO_ROOT / "eval"
RESULTS_DIR = EVAL_DIR / "results"

CANDIDATES = 50
JUDGE_MODEL = "gpt-6-sol"           # a different model from the one answering
JUDGE_TIMEOUT_SECONDS = 60.0
JUDGE_RETRIES = 2                   # the SDK's: a grading call isn't a user waiting. Worst case 3 x 60 s per call
JUDGE_AT_ONCE = 4
WRONG_ANSWER_COST = 2               # a wrong answer costs twice a wrong refusal
SPOT_CHECK_SIZE = 30
# OpenAI's prices per million tokens, as recorded in scripts/generate_corpus.py, scripts/generate_questions.py and
# scripts/try_embedding.py. Only used for estimates
PRICE_PER_MILLION = {
    "gpt-6-luna": {"input": Decimal("0.10"), "output": Decimal("0.50")},
    "gpt-6-sol": {"input": Decimal("2.00"), "output": Decimal("10.00")},
    "text-embedding-3-small": {"input": Decimal("0.02"), "output": Decimal("0")},
}
# Ways of asking and what makes a question hard, the tags retrieval is broken down by
RETRIEVAL_TAGS = ("direct", "keyword", "paraphrase", "colloquial", "vague", "cross_document", "multi_turn",
                  "planning_advice")
REFUSAL_TAGS = ("near_miss", "unauthorized", "out_of_scope")


def graded_for_answer(question: Question) -> bool:
    """A question whose answer can be graded now: from documents alone."""
    return question.expected == "answer" and question.route == "documents"


def scored_for_refusal(question: Question) -> bool:
    """A question in the threshold sweep: one to refuse, or one to answer from documents. A question about numbers
    the system can't answer yet would only count as a false refusal of something not built."""
    return question.expected == "refuse" or graded_for_answer(question)


def price(model: str, input_tokens: int, output_tokens: int) -> Decimal:
    p = PRICE_PER_MILLION[model]
    return (Decimal(input_tokens) * p["input"] + Decimal(output_tokens) * p["output"]) / 1_000_000


# ---------------------------------------------------------------------------
# Running the questions
# ---------------------------------------------------------------------------
def resolve_sections(questions: list[Question]) -> dict[Section, list[int]]:
    sections = {s for q in questions for s in (*q.relevant, *q.hidden)}
    with db.connect() as conn:
        found = {section: resolve(conn, section) for section in sections}
    missing = [s for s, ids in found.items() if not ids]
    if missing:
        raise SystemExit(f"{len(missing)} labelled sections have no chunks: is the corpus ingested? first: {missing[0]}")
    return found


async def ask_all(questions: list[Question], chunks_of: dict[Section, list[int]], client: AsyncOpenAI,
                  instructions: str) -> list[dict]:
    pool = db.query_pool()
    await pool.open()
    limit = ModelCallLimit(settings.model_max_concurrent_calls, settings.model_slot_wait_seconds)
    service = QueryService(
        OpenAIQueryEmbedder(client, settings.embedding_model, limit, settings.query_embedding_timeout_seconds),
        OpenAIAnswerModel(client, settings.chat_model, limit, settings.query_model_timeout_seconds,
                          settings.answer_max_output_tokens),
        pool, min_similarity=0.0, top_k=settings.query_top_k, instructions=instructions)
    encoding = tiktoken.get_encoding(TOKEN_ENCODING)
    records = []
    try:
        for number, question in enumerate(questions, 1):
            records.append(await ask_one(service, question, chunks_of, encoding))
            print(f"\r{number}/{len(questions)}", end="", flush=True)
        print()
    finally:
        await pool.close()
    return records


async def ask_one(service: QueryService, question: Question, chunks_of: dict[Section, list[int]], encoding) -> dict:
    record = {"id": question.id, "question": question.question, "asker": question.asker.model_dump(),
              "route": question.route, "expected": question.expected, "tags": list(question.tags),
              "answer_points": list(question.answer_points), "embedding_tokens": len(encoding.encode(question.question))}
    started = time.perf_counter()
    try:
        async with asyncio.timeout(settings.query_budget_seconds):
            traced = await service.trace(question.question, role=question.asker.role,
                                         department=question.asker.department,
                                         budget=TimeBudget(settings.query_budget_seconds), candidates=CANDIDATES)
    except (TimeoutError, APIError, ModelBusy, psycopg.Error) as error:
        # Nothing comes back from these, not even the search: the question is counted as an error everywhere
        return record | {"status": metrics.ERROR, "error": f"{type(error).__name__}: {error}"[:300],
                         "total_ms": (time.perf_counter() - started) * 1000}

    candidate_ids = [c.chunk_id for c in traced.candidates]
    hidden_ids = {i for s in question.hidden for i in chunks_of[s]}
    prompt_chunks = traced.candidates[:settings.query_top_k]
    record |= {
        "status": metrics.ERROR if traced.error else traced.result.status,
        "error": str(traced.error) if traced.error else None,
        "answer": traced.result.text if traced.result else None,
        "citations": [c.number for c in traced.result.citations] if traced.result else [],
        "top_score": traced.top_score,
        "excerpts": [{"title": c.title, "department": c.department, "heading_path": list(c.heading_path),
                      "score": round(c.score, 4), "content": c.content} for c in prompt_chunks],
        "section_ranks": [metrics.section_ranks(candidate_ids, [chunks_of[s]])[0] for s in question.relevant],
        "hidden_retrieved": len(hidden_ids & set(candidate_ids)),
        "embed_ms": traced.embed_ms, "search_ms": traced.search_ms, "model_ms": traced.model_ms,
        "input_tokens": traced.input_tokens, "output_tokens": traced.output_tokens,
    }
    return record


async def grade_all(records: list[dict], judge: OpenAIJudge) -> None:
    semaphore = asyncio.Semaphore(JUDGE_AT_ONCE)

    async def one(record: dict) -> None:
        async with semaphore:
            try:
                result = await grade(judge, record["question"], record["answer"],
                                     [e["content"] for e in record["excerpts"]], record["answer_points"])
            except (JudgeError, APIError, KeyError) as error:
                record["grade_error"] = f"{type(error).__name__}: {error}"[:300]
                return
        record["claims"] = [{"claim": v.text, "supported": v.ok, "reason": v.reason} for v in result.claims]
        record["points"] = [{"point": v.text, "covered": v.ok, "reason": v.reason} for v in result.points]
        record["faithfulness"] = result.faithfulness
        record["coverage"] = result.coverage

    await asyncio.gather(*(one(r) for r in records))


# ---------------------------------------------------------------------------
# The numbers
# ---------------------------------------------------------------------------
def pct(value: float | None) -> str:
    return "–" if value is None else f"{value * 100:.1f}%"


def retrieval_rows(records: list[dict]) -> list[tuple[str, metrics.RetrievalScores]]:
    labelled = [r for r in records if r.get("section_ranks")]
    groups: list[tuple[str, list[dict]]] = [("all", labelled)]
    groups += [(f"route: {route}", [r for r in labelled if r["route"] == route]) for route in ("documents", "both")]
    groups += [(tag, [r for r in labelled if tag in r["tags"]]) for tag in RETRIEVAL_TAGS]
    rows = []
    for name, group in groups:
        scores = metrics.retrieval_scores([r["section_ranks"] for r in group])
        if scores:
            rows.append((name, scores))
    return rows


def outcomes(records: list[dict], questions: dict[str, Question]) -> list[metrics.Outcome]:
    return [metrics.Outcome(r["expected"], r["status"], r.get("top_score", 0.0))
            for r in records if scored_for_refusal(questions[r["id"]])]


def summarize(records: list[dict], questions: dict[str, Question], name: str, split: str, instructions: str,
              judge: OpenAIJudge | None, run_seconds: float) -> str:
    lines = [f"# Evaluation run: {split}-{name}", "",
             f"{len(records)} questions from `eval/{split}.jsonl`. Answers by {settings.chat_model}, embeddings "
             f"{settings.embedding_model}, top {settings.query_top_k} excerpts in the prompt, {CANDIDATES} candidates "
             f"searched. The run used a threshold of 0, so every question reached the model. "
             f"{sum(r['status'] == metrics.ERROR for r in records)} ended in an error. {run_seconds:.0f} s in all.", ""]

    # Retrieval
    lines += ["## Retrieval", "",
              "By section: a section counts as found at the rank of its best chunk. Only questions with labelled "
              "sections.", "",
              "| Questions | n | Recall@5 | Recall@50 | MRR |", "|---|---|---|---|---|"]
    rows = retrieval_rows(records)
    lines += [f"| {label} | {s.questions} | {pct(s.recall_at_5)} | {pct(s.recall_at_50)} | {s.mrr:.3f} |"
              for label, s in rows]
    leaks = [r["id"] for r in records if r.get("hidden_retrieved")]
    lines += ["", f"Sections the asker may not see, among the candidates: {len(leaks)} questions"
              + (f" ({', '.join(leaks)})" if leaks else "") + ".", ""]
    overall = dict(rows).get("all")
    keyword = dict(rows).get("keyword")
    if overall and keyword:
        gap = overall.recall_at_5 - keyword.recall_at_5
        lines += [f"Keyword questions: Recall@5 {pct(keyword.recall_at_5)} against {pct(overall.recall_at_5)} for "
                  f"all, a gap of {gap * 100:.1f} points (the rule for adding hybrid search: 10 points or more). "
                  f"Recall@50 for all: {pct(overall.recall_at_50)}.", ""]

    # Threshold
    scored = outcomes(records, questions)
    sweep = metrics.sweep(scored, metrics.threshold_grid())
    chosen = metrics.choose_threshold(sweep, WRONG_ANSWER_COST)
    current = settings.query_min_similarity
    lines += ["## Refusal threshold", "",
              f"{sum(o.expected == 'refuse' for o in scored)} questions to refuse and "
              f"{sum(o.expected == 'answer' for o in scored)} to answer from documents. A wrong answer costs "
              f"{WRONG_ANSWER_COST} times a wrong refusal, weighed on the rates. Lowest cost at **{chosen:.2f}** "
              f"(middle of the widest run of equal cost); the setting is {current:.2f}.", "",
              "| Threshold | Wrong answers | Wrong refusals | Cost |", "|---|---|---|---|"]
    shown = {round(t, 2) for t in metrics.threshold_grid(0.0, 0.7, 0.05)} | {round(chosen, 2), round(current, 2)}
    for r in sweep:
        if round(r.threshold, 2) in shown:
            mark = " ← chosen" if math_close(r.threshold, chosen) else (" ← setting" if math_close(r.threshold, current) else "")
            lines.append(f"| {r.threshold:.2f}{mark} | {r.false_answers}/{r.to_refuse} ({pct(r.false_answer_rate)}) | "
                         f"{r.false_refusals}/{r.to_answer} ({pct(r.false_refusal_rate)}) | "
                         f"{r.cost(WRONG_ANSWER_COST):.3f} |")
    lines += ["", "Questions to refuse that were answered, by kind, at the chosen threshold:", "",
              "| Kind | Answered | Lowest top score among them |", "|---|---|---|"]
    for tag in REFUSAL_TAGS:
        group = [r for r in records if r["expected"] == "refuse" and tag in r["tags"]]
        wrong = [r for r in group if metrics.status_at(metrics.Outcome("refuse", r["status"], r.get("top_score", 0.0)),
                                                       chosen) == metrics.ANSWERED]
        low = min((r["top_score"] for r in wrong), default=None)
        lines.append(f"| {tag} | {len(wrong)}/{len(group)} | {'–' if low is None else f'{low:.3f}'} |")
    lines.append("")

    # Answers
    graded = [r for r in records if graded_for_answer(questions[r["id"]])]
    answered = [r for r in graded if r["status"] == metrics.ANSWERED]
    with_grade = [r for r in answered if r.get("faithfulness") is not None or r.get("coverage") is not None]
    faith = [r["faithfulness"] for r in with_grade if r.get("faithfulness") is not None]
    cover = [r["coverage"] for r in with_grade if r.get("coverage") is not None]
    statuses = Counter(r["status"] for r in graded)
    lines += ["## Answers", "",
              f"{len(graded)} questions to answer from documents: {statuses[metrics.ANSWERED]} answered, "
              f"{statuses[metrics.REFUSED]} refused by the model, {statuses[metrics.ERROR]} errors (threshold 0). "
              f"Graded by {judge.model if judge else 'nobody (--no-judge)'}: {len(with_grade)} answers"
              f"{', ' + str(sum('grade_error' in r for r in answered)) + ' could not be graded' if judge else ''}.", "",
              "| | Mean | All of it |", "|---|---|---|",
              f"| Faithfulness: claims the excerpts support | {pct(metrics.mean(faith))} | "
              f"{sum(f == 1 for f in faith)}/{len(faith)} answers |",
              f"| Coverage: answer points said | {pct(metrics.mean(cover))} | "
              f"{sum(c == 1 for c in cover)}/{len(cover)} answers |", ""]
    by_tag = defaultdict(list)
    for r in with_grade:
        for tag in r["tags"]:
            by_tag[tag].append(r)
    lines += ["| Tag | n | Faithfulness | Coverage |", "|---|---|---|---|"]
    for tag in RETRIEVAL_TAGS:
        group = by_tag.get(tag, [])
        if group:
            lines.append(f"| {tag} | {len(group)} | "
                         f"{pct(metrics.mean(r['faithfulness'] for r in group if r.get('faithfulness') is not None))} | "
                         f"{pct(metrics.mean(r['coverage'] for r in group if r.get('coverage') is not None))} |")
    lines.append("")

    # Latency and cost
    ran = [r for r in records if "embed_ms" in r]   # an error before the trace came back has no timings
    before_model = [r["embed_ms"] + r["search_ms"] for r in ran]
    with_model = [r["embed_ms"] + r["search_ms"] + r["model_ms"] for r in ran]
    at_chosen = [r["embed_ms"] + r["search_ms"] + (r["model_ms"] if r["top_score"] >= chosen else 0) for r in ran]
    lines += ["## Latency and cost", "",
              f"One query at a time, in llm-service, without the backend. {len(ran)} queries.", "",
              "| ms | p50 | p95 | max |", "|---|---|---|---|"]
    for label, values in (("embedding + search", before_model), ("whole query, model always asked", with_model),
                          (f"whole query at threshold {chosen:.2f}", at_chosen),
                          ("model only", [r["model_ms"] for r in ran])):
        if values:
            lines.append(f"| {label} | {metrics.percentile(values, 50):.0f} | {metrics.percentile(values, 95):.0f} | "
                         f"{max(values):.0f} |")
    answer_cost = sum((price(settings.chat_model, r["input_tokens"], r["output_tokens"]) for r in ran), Decimal(0))
    embed_cost = sum((price(settings.embedding_model, r["embedding_tokens"], 0) for r in ran), Decimal(0))
    if ran:
        per_query = (answer_cost + embed_cost) / len(ran)
        lines += ["", f"Cost per query, model always asked: ${per_query:.6f} (${per_query * 1000:.3f} per thousand); "
                  f"input {metrics.mean(r['input_tokens'] for r in ran):.0f} and output "
                  f"{metrics.mean(r['output_tokens'] for r in ran):.0f} tokens on average."]
    if judge:
        lines.append(f"Grading: {judge.input_tokens:,} input and {judge.output_tokens:,} output tokens, about "
                     f"${price(judge.model, judge.input_tokens, judge.output_tokens):.2f}.")
    lines += ["", "## Answer instructions", "", "```", instructions, "```", ""]
    return "\n".join(lines)


def math_close(a: float, b: float) -> bool:
    return abs(a - b) < 1e-9


def spot_check(records: list[dict], questions: dict[str, Question], seed: str) -> str:
    graded = [r for r in records if graded_for_answer(questions[r["id"]]) and r["status"] == metrics.ANSWERED
              and "claims" in r]
    picked = metrics.sample(graded, SPOT_CHECK_SIZE, seed, key=lambda r: r["id"])
    lines = ["# Spot check", "",
             f"{len(picked)} graded answers picked at random (seed `{seed}`). For each, say whether you agree with the "
             "judge's verdicts; where you don't, say which claim or point and why.", ""]
    for number, r in enumerate(picked, 1):
        lines += [f"## {number}. `{r['id']}` · {r['asker']['role']} in {r['asker']['department'] or 'all departments'}",
                  "", f"**Question:** {r['question']}", "", "**Answer:**", "", r["answer"], "", "**Excerpts cited:**", ""]
        for n in r["citations"]:
            e = r["excerpts"][n - 1]
            lines.append(f"- [{n}] {e['title']} › {' › '.join(e['heading_path'])} ({e['department']})")
        lines += ["", f"**Claims** (faithfulness {pct(r['faithfulness'])}):", ""]
        lines += [f"- {'✅' if c['supported'] else '❌'} {c['claim']}" + ("" if c["supported"] else f" — {c['reason']}")
                  for c in r["claims"]]
        lines += ["", f"**Answer points** (coverage {pct(r['coverage'])}):", ""]
        lines += [f"- {'✅' if p['covered'] else '❌'} {p['point']}" + ("" if p["covered"] else f" — {p['reason']}")
                  for p in r["points"]]
        lines += ["", "Your verdict: ", ""]
    return "\n".join(lines)


async def main_async(args: argparse.Namespace) -> None:
    db.require_compatible_event_loop()
    questions = load(EVAL_DIR / f"{args.split}.jsonl")
    if args.limit:
        questions = metrics.sample(questions, args.limit, f"limit-{args.limit}", key=lambda q: q.id)
    by_id = {q.id: q for q in questions}
    chunks_of = resolve_sections(questions)
    instructions = Path(args.instructions).read_text(encoding="utf-8").strip() if args.instructions else INSTRUCTIONS

    started = time.perf_counter()
    client = openai_client_from_settings()
    judge_client = AsyncOpenAI(api_key=settings.openai_api_key, base_url=settings.openai_base_url,
                               max_retries=JUDGE_RETRIES)
    judge = None
    try:
        records = await ask_all(questions, chunks_of, client, instructions)
        if not args.no_judge:
            judge = OpenAIJudge(judge_client, JUDGE_MODEL, JUDGE_TIMEOUT_SECONDS)
            to_grade = [r for r in records if graded_for_answer(by_id[r["id"]]) and r["status"] == metrics.ANSWERED]
            print(f"grading {len(to_grade)} answers with {JUDGE_MODEL}")
            await grade_all(to_grade, judge)
    finally:
        await client.close()
        await judge_client.close()

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    stem = RESULTS_DIR / f"{args.split}-{args.name}"
    with stem.with_suffix(".jsonl").open("w", encoding="utf-8", newline="\n") as out:
        for record in records:
            out.write(json.dumps(record, ensure_ascii=False) + "\n")
    report = summarize(records, by_id, args.name, args.split, instructions, judge, time.perf_counter() - started)
    stem.with_suffix(".md").write_text(report, encoding="utf-8", newline="\n")
    if judge:
        Path(f"{stem}-spot-check.md").write_text(spot_check(records, by_id, f"{args.split}-{args.name}"),
                                                 encoding="utf-8", newline="\n")
    print(report)
    print(f"written: {stem}.jsonl, {stem.name}.md" + (f", {stem.name}-spot-check.md" if judge else ""))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--split", choices=("dev", "test"), default="dev")
    parser.add_argument("--name", required=True, help="names the result files: eval/results/<split>-<name>.*")
    parser.add_argument("--limit", type=int, help="only this many questions, picked at random: a quick check")
    parser.add_argument("--no-judge", action="store_true", help="skip grading the answers (no grading cost)")
    parser.add_argument("--instructions", help="a file of answer instructions to try instead of the service's")
    args = parser.parse_args()
    if args.split == "test" and args.instructions:
        parser.error("test only reports final numbers: try instructions on dev")
    choose_event_loop()
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
