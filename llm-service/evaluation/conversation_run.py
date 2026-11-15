"""Conversations through the whole query path: follow-ups, campaigns and times named relative to now, questions to ask
back, and questions about another department's campaign, each checked against what it should come to. Two files, kept
apart the way dev and test are: eval/conversations-dev.jsonl is for tuning the rewrite (--questions dev),
eval/conversations.jsonl only checks it (the default) and nothing is tuned on it.

    uv run python -m evaluation.conversation_run --questions dev --name 1
    uv run python -m evaluation.conversation_run --name 1

Each question carries one expectation, checked by code:
  basis           the answer is about this campaign (department, name, start date): "the last one" was read right,
                  or a follow-up about a campaign an answer already named was written out with its name and dates
  clarify         asked back, with one candidate from each of these departments
  refused         refused, about no campaign: another department's campaign reads as one that doesn't exist
  as_asked        not rewritten: "next time", a batch, a campaign named by its date
  asked_contains  rewritten, and the question that ran holds every one of these: a follow-up written out, or the dates
                  of "last month" worked out

A leak is a campaign of a department the asker may not see, in the basis, among the candidates, or named in the answer
or its rows; it counts whatever the expectation. A follow-up is asked with the turns its record has, as the conversation
memory would give them. Every labelled campaign is looked up first: it must exist, have finished by the evaluation's
date, and be one the asker may see, or the run stops before spending anything.

Needs Postgres with the mock data, sql_reader enabled and OPENAI_API_KEY. Writes
eval/results/conversations-<questions>-<name>.jsonl and .md.
"""
import argparse
import asyncio
import json
import time
from collections import Counter
from datetime import date
from decimal import Decimal
from typing import Literal

import psycopg
from openai import APIError
from pydantic import model_validator

from app import db
from app.config import settings
from app.main import query_flow_from_settings
from app.query_flow import CLARIFY, FlowTrace, QueryFlow
from app.rag.answering import REFUSED
from app.rag.query_models import ModelBusy, openai_client_from_settings
from app.rag.reranking import tei_reranker_from_settings
from app.router.rewriting import Turn as RouterTurn
from app.server import choose_event_loop
from app.time_budget import TimeBudget
from evaluation import metrics
from evaluation.dataset import Asker, Record, Turn, visible
from evaluation.flow_run import cost
from evaluation.run import EVAL_DIR, RESULTS_DIR, pct
from scripts.generate_mock_data import AS_OF

QUESTION_FILES = {"check": EVAL_DIR / "conversations.jsonl", "dev": EVAL_DIR / "conversations-dev.jsonl"}
KINDS = ("follow_up", "last_campaign", "period", "ask_back", "another_department", "as_asked")
Kind = Literal["follow_up", "last_campaign", "period", "ask_back", "another_department", "as_asked"]
TIMEOUT, ERROR = "timeout", "error"


class CampaignLabel(Record):
    department: str
    name: str
    start: date

    def text(self) -> str:
        return f"{self.name} ({self.start.isoformat()})"


class Expect(Record):
    basis: CampaignLabel | None = None
    clarify: tuple[str, ...] = ()           # the departments the candidates come from, every one of them
    refused: bool = False
    as_asked: bool = False
    asked_contains: tuple[str, ...] = ()

    @model_validator(mode="after")
    def one_expectation(self):
        if sum((self.basis is not None, bool(self.clarify), self.refused, self.as_asked, bool(self.asked_contains))) != 1:
            raise ValueError("a conversation expects exactly one thing")
        return self

    def text(self) -> str:
        if self.basis:
            return f"about {self.basis.text()}"
        if self.clarify:
            return "asked back: " + ", ".join(self.clarify)
        if self.refused:
            return "refused"
        if self.as_asked:
            return "as asked"
        return "rewritten with " + ", ".join(f'"{w}"' for w in self.asked_contains)


# Which expectations each kind may have: what the kind is about decides how it is checked
_KIND_EXPECTS = {"follow_up": ("basis", "asked_contains"), "last_campaign": ("basis",), "period": ("asked_contains",),
                 "ask_back": ("clarify",), "another_department": ("refused",), "as_asked": ("as_asked",)}


class Conversation(Record):
    id: str
    asker: Asker
    turns: tuple[Turn, ...] = ()
    question: str
    kind: Kind
    expect: Expect
    source: str
    note: str | None = None

    @model_validator(mode="after")
    def consistent(self):
        set_ = next(name for name in ("basis", "clarify", "refused", "as_asked", "asked_contains")
                    if getattr(self.expect, name))
        if set_ not in _KIND_EXPECTS[self.kind]:
            raise ValueError(f"a {self.kind} question can't expect {set_}")
        if self.kind == "follow_up" and not self.turns:
            raise ValueError("a follow-up needs the turns before it")
        if self.expect.basis and not visible(self.asker, self.expect.basis.department):
            raise ValueError("the campaign expected must be one the asker may see")
        if self.expect.clarify and self.asker.role not in ("executive", "admin"):
            raise ValueError("only executives and admins are asked which department they mean")
        return self


def load(path) -> list[Conversation]:
    with open(path, encoding="utf-8") as lines:
        conversations = [Conversation.model_validate_json(line) for line in lines if line.strip()]
    ids = [c.id for c in conversations]
    if len(set(ids)) != len(ids):
        raise ValueError("two conversations share an id")
    return conversations


_CAMPAIGN = """SELECT c.status, c.end_date FROM campaigns c JOIN departments d USING (department_id)
               WHERE d.name = %s AND c.name = %s AND c.start_date = %s"""


def label_problems(conn, conversations: list[Conversation], today: date = AS_OF) -> list[str]:
    """Every labelled campaign exists and had finished by today. Its being visible to the asker is checked on load."""
    problems = []
    for c in conversations:
        label = c.expect.basis
        if label is None:
            continue
        found = conn.execute(_CAMPAIGN, (label.department, label.name, label.start)).fetchone()
        if found is None:
            problems.append(f"{c.id}: no {label.department} campaign {label.text()}")
        elif found[0] != "finished" or found[1] >= today:
            problems.append(f"{c.id}: {label.text()} hadn't finished by {today.isoformat()}")
    return problems


def hidden_names(conn, asker: Asker) -> set[str]:
    """The names of the campaigns the asker may not see. Every name starts with its department's, so none is shared."""
    rows = conn.execute("SELECT d.name, c.name FROM campaigns c JOIN departments d USING (department_id)").fetchall()
    return {name for department, name in rows if not visible(asker, department)}


def checked(conversation: Conversation, traced: FlowTrace, hidden: set[str]) -> tuple[bool, list[str]]:
    """Whether the question came to what it should, and the leaks: campaigns of departments the asker may not see."""
    answer, expect = traced.answer, conversation.expect
    changed = traced.rewritten is not None and traced.rewritten.changed
    basis = answer.basis if answer else None
    candidates = answer.candidates if answer else ()
    if expect.basis:
        found = basis is not None and (basis.department, basis.name, basis.start_date) == (
            expect.basis.department, expect.basis.name, expect.basis.start)
        # A follow-up about a campaign an answer already named is written out with its name and dates, not looked up
        named = (basis is None and changed and expect.basis.name in traced.asked
                 and expect.basis.start.isoformat() in traced.asked)
        right = found or named
    elif expect.clarify:
        right = (answer is not None and answer.status == CLARIFY
                 and sorted({c.department for c in candidates}) == sorted(expect.clarify))
    elif expect.refused:
        right = answer is not None and answer.status == REFUSED and basis is None
    elif expect.as_asked:
        right = not changed and answer is not None and answer.status != CLARIFY
    else:
        right = changed and all(word.lower() in traced.asked.lower() for word in expect.asked_contains)
    leaks = [f"basis {basis.name}"] if basis is not None and not visible(conversation.asker, basis.department) else []
    leaks += [f"candidate {c.name}" for c in candidates if not visible(conversation.asker, c.department)]
    if answer is not None:
        said = answer.text + "".join(" ".join(v or "" for v in row) for row in (answer.result.rows if answer.result else ()))
        leaks += [f"names {name}" for name in sorted(hidden) if name in said]
    return right, leaks


async def ask(flow: QueryFlow, conversation: Conversation, hidden: set[str], budget_seconds: float) -> dict:
    record = {"id": conversation.id, "kind": conversation.kind, "asker": conversation.asker.model_dump(),
              "question": conversation.question, "turns": len(conversation.turns), "expected": conversation.expect.text()}
    started = time.perf_counter()
    try:
        async with asyncio.timeout(budget_seconds):
            traced = await flow.trace(conversation.question, role=conversation.asker.role,
                                      department=conversation.asker.department, today=AS_OF,
                                      budget=TimeBudget(budget_seconds),
                                      turns=tuple(RouterTurn(t.role, t.content) for t in conversation.turns))
    except TimeoutError:
        return record | {"status": TIMEOUT, "right": False, "leaks": [], "total_ms": (time.perf_counter() - started) * 1000}
    except (APIError, ModelBusy, psycopg.Error) as error:
        return record | {"status": ERROR, "right": False, "leaks": [], "error": f"{type(error).__name__}: {error}"[:300],
                         "total_ms": (time.perf_counter() - started) * 1000}
    right, leaks = checked(conversation, traced, hidden)
    answer, rewritten = traced.answer, traced.rewritten
    record |= {
        "right": right, "leaks": leaks,
        "status": answer.status if answer else "invalid",
        "rewritten": rewritten.standalone if rewritten else None,
        "rewrite_reason": rewritten.reason if rewritten else None,
        "asked": traced.asked,
        "basis": [answer.basis.department, answer.basis.name, answer.basis.start_date.isoformat()]
        if answer and answer.basis else None,
        "candidates": [[c.department, c.name, c.start_date.isoformat()] for c in answer.candidates] if answer else [],
        "paths": list(answer.paths) if answer else [],
        "answer": answer.text if answer else None,
        "rewrite_ms": traced.rewrite_ms, "total_ms": traced.total_ms,
        "tokens": {part: list(used) for part, used in traced.parts().items()},
    }
    record["cost_usd"] = str(cost(record["tokens"]))
    return record


def summarize(records: list[dict], questions: str, name: str, run_seconds: float) -> str:
    right = sum(r["right"] for r in records)
    leaks = [r for r in records if r["leaks"]]
    costs = [Decimal(r["cost_usd"]) for r in records if "cost_usd" in r]
    times = [r["total_ms"] for r in records if r["status"] not in (TIMEOUT, ERROR)]
    lines = [f"# Conversation evaluation run: conversations-{questions}-{name}", "",
             f"{len(records)} questions from `{QUESTION_FILES[questions].relative_to(EVAL_DIR.parent).as_posix()}`, "
             f"through app/query_flow.py with each follow-up's turns, one at a time, as of {AS_OF.isoformat()}. "
             f"{run_seconds:.0f} s in all.", "",
             f"**Right: {right} of {len(records)}** ({pct(right / len(records)) if records else '–'}). "
             f"**Leaks: {sum(len(r['leaks']) for r in records)}** in {len(leaks)} questions. "
             f"Statuses: " + ", ".join(f"{k} {v}" for k, v in Counter(r["status"] for r in records).most_common()) + ".",
             "", "| Kind | Questions | Right |", "|---|---|---|"]
    for kind in KINDS:
        group = [r for r in records if r["kind"] == kind]
        if group:
            lines.append(f"| {kind} | {len(group)} | {sum(r['right'] for r in group)} |")
    if times:
        lines += ["", f"Total ms, p50 / p95 / max: {metrics.percentile(times, 50):.0f} / {metrics.percentile(times, 95):.0f}"
                      f" / {max(times):.0f}. Cost: ${sum(costs):.4f}, the calls that finished only."]
    lines += ["", "| Question | Kind | Expected | Rewritten | Came to | Right |", "|---|---|---|---|---|---|"]
    for r in records:
        came = (f"about {r['basis'][1]} ({r['basis'][2]})" if r.get("basis")
                else "asked back: " + ", ".join(c[0] for c in r["candidates"]) if r.get("candidates")
                else r["status"])
        rewritten = (r.get("asked") or "").replace("|", "/") if r.get("rewritten") else f"– ({r.get('rewrite_reason')})"
        lines.append(f"| `{r['id']}` {r['question'].replace('|', '/')} | {r['kind']} | {r['expected']} | {rewritten} | "
                     f"{came}{'; LEAK: ' + ', '.join(r['leaks']) if r['leaks'] else ''} | "
                     f"{'yes' if r['right'] else '**no**'} |")
    return "\n".join(lines) + "\n"


async def main_async(args: argparse.Namespace) -> None:
    db.require_compatible_event_loop()
    if not settings.openai_api_key:
        raise SystemExit("OPENAI_API_KEY is not set: add it to .env at the repository root")
    conversations = load(QUESTION_FILES[args.questions])
    with db.connect() as conn:
        problems = label_problems(conn, conversations)
        hidden = {c.id: hidden_names(conn, c.asker) for c in conversations}
    if problems:
        raise SystemExit("labels that don't match the data:\n  " + "\n  ".join(problems))
    client = openai_client_from_settings()
    pool, reader_pool = db.query_pool(), db.sql_pool()
    await pool.open()
    await reader_pool.open()
    reranker = tei_reranker_from_settings()
    flow = query_flow_from_settings(client, pool, reader_pool, reranker)
    started, records = time.perf_counter(), []
    try:
        for number, conversation in enumerate(conversations, 1):
            records.append(await ask(flow, conversation, hidden[conversation.id], settings.query_budget_seconds))
            print(f"\r{number}/{len(conversations)}", end="", flush=True)
        print()
    finally:
        await pool.close()
        await reader_pool.close()
        await client.close()
        if reranker:
            await reranker.aclose()
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    stem = RESULTS_DIR / f"conversations-{args.questions}-{args.name}"
    with stem.with_suffix(".jsonl").open("w", encoding="utf-8", newline="\n") as f:
        for record in records:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    stem.with_suffix(".md").write_text(summarize(records, args.questions, args.name, time.perf_counter() - started),
                                       encoding="utf-8", newline="\n")
    print(f"written: {stem}.jsonl, {stem.name}.md")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--questions", choices=tuple(QUESTION_FILES), default="check")
    parser.add_argument("--name", required=True, help="names the result files: eval/results/conversations-<questions>-<name>.*")
    choose_event_loop()
    asyncio.run(main_async(parser.parse_args()))


if __name__ == "__main__":
    main()
