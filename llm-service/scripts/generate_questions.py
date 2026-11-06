"""Writes the candidate questions of the evaluation dataset into eval/candidates.jsonl, from the ingested corpus.

Every question comes from a task: a few sections of the corpus (or the schema, for questions about numbers), who is
asking, and what kind of question it should be. A model writes the questions and says which of the sections it showed
hold each answer, so every label is a real section of the corpus. Then each question is checked before it is kept:
the numbers of its answer points must be in those sections, its SQL must run on the development database and return
rows, and a question the asker must not get an answer to must point at a document the asker really can't see.

The model is not the one that answers (gpt-6-sol here, gpt-6-luna in the service), so the questions aren't written
in the answering model's own words. Candidates are reviewed by a person before they become the dataset.

    cd llm-service
    uv run python -m scripts.generate_questions --dry-run --pilot   # the tasks, no model call
    uv run python -m scripts.generate_questions --pilot             # one task of every kind, to judge the quality
    uv run python -m scripts.generate_questions --all               # every task not done yet
    uv run python -m scripts.generate_questions --report            # what the candidates cover

Needs the corpus ingested (scripts.ingest_corpus). Tasks already in the file are skipped, so it can run again.
"""
import argparse
import hashlib
import json
import random
import re
import sys
from dataclasses import dataclass, replace
from datetime import date
from decimal import Decimal
from pathlib import Path

import psycopg
from openai import OpenAI
from pydantic import ValidationError

from app import db
from app.config import settings
from evaluation.dataset import Asker, DocumentRef, Question, Section, Turn, load, save, split_for, visible
from scripts.generate_corpus import stray_numbers
from scripts.generate_mock_data import AS_OF, DEPARTMENTS

REPO_ROOT = Path(__file__).resolve().parents[2]
OUT = REPO_ROOT / "eval" / "candidates.jsonl"

MODEL = "gpt-6-sol"
# OpenAI's price for gpt-6-sol, checked 2026-11-05, per million tokens. Only used to print an estimate
PRICE_PER_MILLION = {"input": Decimal("2.00"), "output": Decimal("10.00")}
REQUEST_TIMEOUT_SECONDS = 180
MAX_OUTPUT_TOKENS = 6000
SQL_TIMEOUT_MS = 5000
MAX_RESULT_ROWS = 200


# ---------------------------------------------------------------------------
# The corpus as ingested
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Chunk:
    section: Section
    label: str          # how the prompt names the document
    text: str


def load_corpus(conn) -> list[Chunk]:
    rows = conn.execute(
        """SELECT d.doc_type, d.department, d.title, c.name, c.start_date, ch.metadata->'heading_path', ch.content
           FROM doc_chunks ch JOIN documents d USING (doc_id) LEFT JOIN campaigns c ON c.campaign_id = d.campaign_id
           ORDER BY d.doc_type, d.department, coalesce(c.name, d.title), c.start_date, ch.chunk_index""").fetchall()
    chunks = []
    for doc_type, department, title, campaign, start, path, content in rows:
        if doc_type == "sop":
            ref = DocumentRef(doc_type="sop", department=department, title=title)
            label = f"SOP, {department}: {title}"
        else:
            ref = DocumentRef(doc_type=doc_type, department=department, campaign=campaign, campaign_start=start)
            label = f"{'Campaign plan' if doc_type == 'campaign_plan' else 'Post-campaign review'}, {department}: {campaign} starting {start}"
        chunks.append(Chunk(Section(document=ref, heading_path=tuple(path)), label, content))
    return chunks


def askers(conn) -> dict[str, Asker]:
    found = {"executive": Asker(department=None, role="executive")}
    for role, department in conn.execute(
            """SELECT DISTINCT u.role, d.name FROM users u JOIN departments d USING (department_id)
               WHERE u.role IN ('staff', 'manager')""").fetchall():
        found[f"{role}:{department}"] = Asker(department=department, role=role)
    return found


# ---------------------------------------------------------------------------
# Tasks: what to ask about, who asks, and what kind of question
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Task:
    id: str                       # stable; also the group, and so the split, of its questions
    kind: str
    chunks: tuple[Chunk, ...]
    asker: Asker
    count: int
    theme: str = ""               # for questions about numbers: what their SQL needs
    styles: tuple[str, ...] = ()  # one per question; otherwise the model picks
    repeated: tuple[tuple[str, tuple[date, ...]], ...] = ()   # its campaign names that more than one campaign has,
                                                              # with every start date of that name
    need_day: tuple[date, ...] = ()   # starts of its campaigns that another of the name shares the month of


KINDS = ("fact", "cross_document", "near_miss", "unauthorized", "out_of_scope", "data", "both", "multi_turn")
# Keyword questions are what the decision about hybrid search rests on, and paraphrases what vector search is for,
# so a fact's two questions are written in two of these, chosen by the seed
FACT_STYLES = ["direct", "keyword", "paraphrase", "colloquial"]


def doc_key(chunk: Chunk) -> DocumentRef:
    return chunk.section.document


def documents(corpus: list[Chunk]) -> dict[DocumentRef, list[Chunk]]:
    by_doc: dict[DocumentRef, list[Chunk]] = {}
    for chunk in corpus:
        by_doc.setdefault(doc_key(chunk), []).append(chunk)
    return by_doc


def staff_for(people: dict[str, Asker], department: str, rng: random.Random) -> Asker:
    return people[f"staff:{department if department != 'ALL' else rng.choice(sorted(DEPARTMENTS))}"]


def find(by_doc, *, doc_type: str, department: str | None = None, title_has: str = "", campaign: str = "",
         start: date | None = None) -> DocumentRef:
    matches = [ref for ref in by_doc if ref.doc_type == doc_type and (department is None or ref.department == department)
               and title_has.lower() in (ref.title or "").lower() and campaign in (ref.campaign or "")
               and (start is None or ref.campaign_start == start)]
    if len(matches) != 1:
        raise LookupError(f"{len(matches)} documents match {doc_type} {department} {title_has!r} {campaign!r} {start}")
    return matches[0]


def plan_tasks(corpus: list[Chunk], people: dict[str, Asker],
               campaign_starts: dict[str, tuple[date, ...]] | None = None) -> list[Task]:
    """The same corpus always gives the same tasks. campaign_starts: every start date of every campaign name."""
    starts = campaign_starts or {}
    planned = []
    for task in _plan_tasks(corpus, people):
        refs = {c.section.document for c in task.chunks if c.section.document.campaign}
        repeated = sorted({(ref.campaign, starts[ref.campaign]) for ref in refs if len(starts.get(ref.campaign, ())) > 1})
        need_day = sorted({ref.campaign_start for ref in refs
                           if any(d != ref.campaign_start and (d.year, d.month) == (ref.campaign_start.year, ref.campaign_start.month)
                                  for d in starts.get(ref.campaign, ()))})
        planned.append(replace(task, repeated=tuple(repeated), need_day=tuple(need_day)))
    return planned


def _plan_tasks(corpus: list[Chunk], people: dict[str, Asker]) -> list[Task]:
    rng = random.Random("questions-v1")
    by_doc = documents(corpus)
    tasks: list[Task] = []

    # ---- facts in one section: 30 of SOPs, 8 of plans, 12 of reviews; two questions each
    def sample(doc_type, n):
        pool = [c for c in corpus if doc_key(c).doc_type == doc_type and len(c.text.split()) >= 60]
        return rng.sample(pool, n)
    for chunk in sample("sop", 30) + sample("campaign_plan", 8) + sample("campaign_review", 12):
        styles = tuple(rng.sample(FACT_STYLES, 2))
        tasks.append(Task(f"fact:{task_name(chunk)}", "fact", (chunk,), staff_for(people, doc_key(chunk).department, rng), 2,
                          styles=styles))

    # ---- across documents: the corpus's distractors, asked side by side
    executive = people["executive"]
    pairs = []
    for base in ("Post-Livestream Review Process", "Sale Event Inventory Check", "Host Onboarding and Review"):
        twins = sorted((ref for ref in by_doc if ref.doc_type == "sop" and base.lower() in ref.title.lower()),
                       key=lambda r: r.department)
        pairs.append((f"twins {base}", rng.sample(twins, 2), executive))
    pairs += [
        ("black friday 2025 and 2026", [find(by_doc, doc_type="sop", title_has="Readiness Checklist 2025"),
                                        find(by_doc, doc_type="sop", title_has="Readiness Checklist 2026")], people["staff:Beauty"]),
        ("price lock and its exception", [find(by_doc, doc_type="sop", title_has="Promotional Pricing"),
                                          find(by_doc, doc_type="sop", title_has="Readiness Checklist 2026")], people["staff:Home Goods"]),
        ("refunds and spoilage claims", [find(by_doc, doc_type="sop", title_has="Refund and Return"),
                                         find(by_doc, doc_type="sop", title_has="Cold Chain")], people["staff:Fresh Grocery"]),
        ("discount approval and bundles", [find(by_doc, doc_type="sop", title_has="Promotional Pricing"),
                                           find(by_doc, doc_type="sop", title_has="Bundle Pricing")], people["staff:Home Goods"]),
        ("two january launches", [find(by_doc, doc_type="campaign_review", campaign="Home New Product Launch", start=date(2026, 1, 8)),
                                  find(by_doc, doc_type="campaign_review", campaign="Home New Product Launch", start=date(2026, 1, 18))],
         people["staff:Home Goods"]),
    ]
    planned = [ref for ref in by_doc if ref.doc_type == "campaign_plan"
               and any(r.doc_type == "campaign_review" and r.campaign == ref.campaign and r.campaign_start == ref.campaign_start for r in by_doc)]
    for plan in rng.sample(sorted(planned, key=str), 2):
        review = find(by_doc, doc_type="campaign_review", campaign=plan.campaign, start=plan.campaign_start)
        pairs.append((f"plan and review {plan.campaign} {plan.campaign_start}", [plan, review], people[f"staff:{plan.department}"]))
    for name, refs, asker in pairs:
        chunks = tuple(c for ref in refs for c in by_doc[ref])
        tasks.append(Task(f"cross_document:{name}", "cross_document", chunks, asker, 2))

    # ---- near misses: whole documents, and questions they don't answer
    for ref in rng.sample([r for r in by_doc if r.doc_type == "sop"], 3) + \
               rng.sample([r for r in by_doc if r.doc_type != "sop"], 2):
        tasks.append(Task(f"near_miss:{doc_name(ref)}", "near_miss", tuple(by_doc[ref]), staff_for(people, ref.department, rng), 3))

    # ---- unauthorized: a department's document, asked about by another department's staff
    own = [r for r in by_doc if r.department != "ALL"]
    twins = [r for r in own if r.doc_type == "sop" and any(base.lower() in r.title.lower() for base in
                                                          ("Post-Livestream Review", "Sale Event Inventory", "Host Onboarding"))]
    for ref in rng.sample(sorted(twins, key=str), 3) + rng.sample(sorted(set(own) - set(twins), key=str), 2):
        other = rng.choice([d for d in sorted(DEPARTMENTS) if d != ref.department])
        tasks.append(Task(f"unauthorized:{doc_name(ref)}:{other}", "unauthorized", tuple(by_doc[ref]), people[f"staff:{other}"], 3))

    # ---- out of scope
    # Trivia such as "what is the capital of Canada" is refused by anything; these are what staff do type at work
    tasks.append(Task("out_of_scope:general knowledge", "out_of_scope", (), people["staff:Beauty"], 5,
                      theme="things a member of staff might ask a work assistant that no company document or company "
                            "data answers: general business knowledge, other companies, public rules such as sales tax "
                            "or a carrier's shipping rules, or how to use a third-party tool"))
    tasks.append(Task("out_of_scope:workplace", "out_of_scope", (), people["staff:Consumer Electronics"], 5,
                      theme="workplace topics no operations document covers, such as payroll, IT support, parental leave or office access"))

    # ---- numbers: SQL
    for key, theme in (("staff:Beauty", "single_table"), ("staff:Beauty", "time_range"),
                       ("staff:Consumer Electronics", "multi_table"), ("staff:Consumer Electronics", "vague"),
                       ("staff:Fresh Grocery", "single_table"), ("staff:Fresh Grocery", "multi_table"),
                       ("staff:Home Goods", "time_range"), ("staff:Home Goods", "vague"),
                       ("manager:Beauty", "multi_table"), ("executive", "multi_table"), ("executive", "time_range")):
        tasks.append(Task(f"data:{key}:{theme}", "data", (), people[key], 5, theme=theme))

    # ---- documents and numbers together: planning advice, and why a campaign turned out as it did
    reviewed = sorted((r for r in by_doc if r.doc_type == "campaign_review"), key=str)
    for i, ref in enumerate(rng.sample(reviewed, 13)):
        theme = "planning_advice" if i < 8 else "why"
        plan = [r for r in by_doc if r.doc_type == "campaign_plan" and r.campaign == ref.campaign and r.campaign_start == ref.campaign_start]
        chunks = tuple(c for r in [ref, *plan] for c in by_doc[r])
        tasks.append(Task(f"both:{doc_name(ref)}", "both", chunks, people[f"manager:{ref.department}"], 2, theme=theme))

    # ---- follow-ups: four about documents, four about numbers
    for chunk in rng.sample([c for c in corpus if doc_key(c).doc_type == "sop" and len(c.text.split()) >= 60], 4):
        tasks.append(Task(f"multi_turn:{task_name(chunk)}", "multi_turn", (chunk,), staff_for(people, doc_key(chunk).department, rng), 2))
    for key in ("staff:Beauty", "staff:Consumer Electronics", "staff:Fresh Grocery", "executive"):
        tasks.append(Task(f"multi_turn:data:{key}", "multi_turn", (), people[key], 2, theme="data"))
    return tasks


def doc_name(ref: DocumentRef) -> str:
    return ref.title if ref.doc_type == "sop" else f"{ref.doc_type} {ref.campaign} {ref.campaign_start}"


def task_name(chunk: Chunk) -> str:
    return f"{doc_name(doc_key(chunk))} > {' > '.join(chunk.section.heading_path[1:])}"


# The first task of every kind, to judge the quality by before writing the rest
def pilot(tasks: list[Task]) -> list[Task]:
    return [next(t for t in tasks if t.kind == kind) for kind in KINDS]


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------
INSTRUCTIONS = f"""You write questions to evaluate an assistant for the operations staff of a US company that sells
through livestreams. The assistant answers from the company's internal documents (SOPs, campaign plans, post-campaign
reviews) and from its campaign database. Your questions test whether it finds the right document or data and answers
correctly. Today is {AS_OF.isoformat()}.

Write each question the way a member of staff would type it into a chat box at work: one question, no greeting, no
"according to the document". The style you are given for each question decides how it is asked:
- direct: a plain question in the document's own terms
- keyword: hinges on a specific name, term or abbreviation that appears in the text
- paraphrase: asks for the same fact without the document's words; no run of four or more words copied from it
- colloquial: how someone types in a hurry: short, lowercase, maybe an abbreviation
- vague: refers to something imprecisely, such as "last Black Friday" or "our big summer sale", so it has to be worked out

answer_points: 1 to 4 short facts a right answer has to state, each taken from the material, with every number
written exactly as there. sections: the ids of the sections that hold the answer. Return JSON that matches the schema;
leave sql, previous_question and previous_answer null unless the task asks for them."""

SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["questions"],
    "properties": {"questions": {"type": "array", "items": {
        "type": "object", "additionalProperties": False,
        "required": ["question", "style", "answer_points", "sections", "sql", "previous_question", "previous_answer"],
        "properties": {
            "question": {"type": "string"},
            "style": {"type": "string", "enum": ["direct", "keyword", "paraphrase", "colloquial", "vague"]},
            "answer_points": {"type": "array", "items": {"type": "string"}},
            "sections": {"type": "array", "items": {"type": "string"}},
            "sql": {"type": ["string", "null"]},
            "previous_question": {"type": ["string", "null"]},
            "previous_answer": {"type": ["string", "null"]},
        }}}},
}


def section_block(chunks: tuple[Chunk, ...]) -> str:
    return "\n\n".join(f'<section id="S{i}" document="{c.label}" heading="{" > ".join(c.section.heading_path)}">\n{c.text}\n</section>'
                       for i, c in enumerate(chunks, 1))


def asker_text(asker: Asker) -> str:
    if asker.role == "executive":
        return "an executive, who can see every department"
    who = "a member of staff" if asker.role == "staff" else "a manager"
    return f"{who} in the {asker.department} department, who can see only {asker.department} and company-wide documents and data"


def schema_text(conn, asker: Asker) -> str:
    tables = ("departments", "hosts", "campaigns", "campaign_metrics", "campaign_products", "campaign_customer_behavior")
    lines = []
    for table in tables:
        columns = [f"{name} {kind}" for name, kind in conn.execute(
            """SELECT column_name, data_type FROM information_schema.columns
               WHERE table_schema = 'public' AND table_name = %s ORDER BY ordinal_position""", (table,)).fetchall()
                   if name not in ("ip_address", "has_plan_doc", "has_review_doc")]
        lines.append(f"{table}({', '.join(columns)})")
    campaigns = conn.execute(
        """SELECT d.name, c.name, c.start_date, c.end_date, c.status FROM campaigns c JOIN departments d USING (department_id)
           WHERE %(department)s::text IS NULL OR d.name = %(department)s ORDER BY d.name, c.start_date""",
        {"department": asker.department}).fetchall()
    listed = "\n".join(f"- {d}: {n}, {s} to {e}, {st}" for d, n, s, e, st in campaigns)
    return f"""PostgreSQL tables (ids join the obvious way: department_id, host_id, campaign_id):
{chr(10).join(lines)}

Definitions the company uses:
- GMV of a campaign: SUM(campaign_metrics.gmv); conversion rate: AVG(campaign_metrics.conversion_rate); peak
  concurrent viewers: MAX(campaign_metrics.peak_concurrent_viewers)
- ROI: SUM(campaign_products.profit) / campaigns.actual_operating_cost (profit already has the cost of goods taken off)
- rates and margins are fractions: 0.0862 means 8.62%

Campaigns the asker can see:
{listed}"""


def task_prompt(conn, task: Task) -> str:
    who = f"The person asking is {asker_text(task.asker)}."
    n = task.count
    if task.kind == "fact":
        styles = " and ".join(f"question {i} in the {style} style" for i, style in enumerate(task.styles, 1))
        body = (f"Write {n} questions, each answered by the section below: {styles}. For a campaign "
                f"document, ask about what its text says (reasons, findings, roles, recommendations, risks), not "
                f"about numbers that only sit in its tables.\n\n{section_block(task.chunks)}")
    elif task.kind == "cross_document":
        body = (f"Write {n} questions whose answer needs at least two of these documents together, for example a "
                f"comparison or a difference between them. List every section the answer needs.\n\n{section_block(task.chunks)}")
    elif task.kind == "near_miss":
        # In the first batch, told only "close to its topic", a model asked for predictions ("what temperature will
        # the cold room be at next holiday?"), which nobody asks and any assistant refuses
        body = (f"Write {n} questions about the procedure in this document that a member of staff would reasonably "
                f"expect it to answer: a detail of a rule, a threshold, an exception, a deadline or who decides, which "
                f"the document leaves out and which no other operations document would answer either. Not a prediction "
                f"and not a question about results or data. answer_points and sections stay empty.\n\n{section_block(task.chunks)}")
    elif task.kind == "unauthorized":
        body = (f"This document belongs to the {task.chunks[0].section.document.department} department, which the "
                f"asker can't see. Write {n} questions the asker would ask that only this document answers, asking "
                f"about it directly, for example by naming its department. answer_points: what the document says; "
                f"sections: where.\n\n{section_block(task.chunks)}")
    elif task.kind == "out_of_scope":
        body = f"Write {n} questions about {task.theme}. answer_points and sections stay empty."
    elif task.kind == "data":
        body = (f"Write {n} questions about numbers that the database answers and that need {theme_text(task.theme)}. "
                f"For each, write sql: one PostgreSQL SELECT that answers it, using the definitions below, that only "
                f"reads campaigns the asker can see{department_filter(task.asker)}, and returns the columns the answer "
                f"needs (ORDER BY and LIMIT for a top item). answer_points: what the answer says, without numbers you "
                f"would have to compute. sections stay empty.\n\n{schema_text(conn, task.asker)}")
    elif task.kind == "both":
        ask = ("what to do differently when planning the next campaign like this one, so the answer has to name the "
               "results and the review it is based on" if task.theme == "planning_advice"
               else "why this campaign turned out as it did against its targets")
        body = (f"Write {n} questions about {ask}. Each needs both the documents below and numbers from the database. "
                f"sql: one PostgreSQL SELECT for the numbers the answer needs, reading only campaigns the asker can "
                f"see{department_filter(task.asker)}. sections: the ids the answer uses.\n\n"
                f"{schema_text(conn, task.asker)}\n\n{section_block(task.chunks)}")
    else:   # multi_turn
        if task.theme == "data":
            source = (f"sql: one PostgreSQL SELECT that answers the follow-up, reading only campaigns the asker can "
                      f"see{department_filter(task.asker)}.\n\n{schema_text(conn, task.asker)}")
        else:
            source = f"The follow-up is answered by the section below; sections: its id.\n\n{section_block(task.chunks)}"
        body = (f"Write {n} two-turn conversations. previous_question is the first question and previous_answer a "
                f"short right answer to it. question is a follow-up that can only be understood with the first turn, "
                f"such as \"and the one before?\" or \"what about returns?\", and has exactly one sensible reading "
                f"given the first turn. answer_points answer the follow-up. {source}")
    if task.repeated:
        names = "; ".join(f'"{name}" (starting {", ".join(f"{MONTHS[d.month - 1]} {d.day}, {d.year}" for d in dates)})'
                          for name, dates in task.repeated)
        body += (f"\n\nMore than one campaign has the same name: {names}. Every question about one of them says which, "
                 f"by its month or start date, the way people tell them apart; when two of them start in the same "
                 f"month, by the day as well, such as \"Jan 18\".")
    return f"{who}\n\n{body}"


# A month, a year, a date, or a word that orders campaigns: what tells two campaigns of one name apart
WHICH_ONE = re.compile(r"\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\b|\b20\d\d\b|\b\d{4}-\d{2}-\d{2}\b"
                       r"|\b(?:first|second|third|earlier|later|latest|last|previous|next)\b", re.IGNORECASE)


MONTHS = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()


def names_the_day(text: str, day: date) -> bool:
    month = MONTHS[day.month - 1].lower()
    return bool(re.search(rf"\b{month}[a-z]*\.? {day.day}\b|\b{day.isoformat()}\b|\b{day.day}(?:st|nd|rd|th)\b|"
                          rf"\b{day.month}/{day.day}\b", text.lower()))


def theme_text(theme: str) -> str:
    # A staff member's question always joins departments to keep to their campaigns, so "single table" means one
    # kind of data, not one table in the FROM clause
    return {"single_table": "one kind of data only (the campaigns, their daily metrics or their products), with no "
                            "more joins than finding the asker's campaigns needs",
            "multi_table": "combining two or more kinds of data, such as metrics with products or campaigns with hosts",
            "time_range": "a time range, such as a month, a quarter or 'the last three months'",
            "vague": "working out what an imprecise reference means, such as 'last Black Friday' or 'our worst campaign'"}[theme]


def department_filter(asker: Asker) -> str:
    return "" if asker.department is None else f" (filter on departments.name = '{asker.department}')"


# ---------------------------------------------------------------------------
# Checks before a question is kept
# ---------------------------------------------------------------------------
def run_sql(conn, sql: str) -> tuple[tuple[str | None, ...], ...]:
    """In a read-only transaction with a time limit, so a generated query can't change or hang anything."""
    text = sql.strip().rstrip(";")
    if not re.match(r"(?is)^\s*(select|with)\b", text) or ";" in text:
        raise ValueError("not a single SELECT")
    with conn.transaction():
        conn.execute("SET TRANSACTION READ ONLY")
        conn.execute(f"SET LOCAL statement_timeout = {SQL_TIMEOUT_MS}")
        rows = conn.execute(text).fetchall()
    if not rows:
        raise ValueError("returns no rows")
    if len(rows) > MAX_RESULT_ROWS:
        raise ValueError(f"returns {len(rows)} rows")
    return tuple(tuple(None if v is None else str(v) for v in row) for row in rows)


def build(conn, task: Task, item: dict, chunks_by_id: dict[str, Chunk], index: int) -> Question:
    """One question from the model's answer, or ValueError / ValidationError saying why it isn't kept."""
    sections = []
    for sid in item["sections"]:
        if sid not in chunks_by_id:
            raise ValueError(f"names a section it wasn't shown: {sid}")
        sections.append(chunks_by_id[sid].section)
    expected = "refuse" if task.kind in ("near_miss", "unauthorized", "out_of_scope") else "answer"
    route = {"fact": "documents", "cross_document": "documents", "near_miss": "documents", "unauthorized": "documents",
             "out_of_scope": "none", "data": "data", "both": "both",
             "multi_turn": "data" if task.theme == "data" else "documents"}[task.kind]
    tags = [item["style"]]
    if task.kind in ("cross_document", "near_miss", "unauthorized", "out_of_scope", "multi_turn"):
        tags.append(task.kind)
    if task.kind == "data":
        tags.append(task.theme)
    if task.kind == "both" and task.theme == "planning_advice":
        tags.append("planning_advice")

    gold_sql, gold_result = None, None
    if route in ("data", "both"):
        if not item["sql"]:
            raise ValueError("no SQL")
        if task.asker.department and f"'{task.asker.department}'" not in item["sql"]:
            raise ValueError(f"the SQL doesn't keep to the {task.asker.department} department")
        gold_sql, gold_result = item["sql"].strip(), run_sql(conn, item["sql"])
    if route in ("documents", "both") and expected == "answer":
        material = "\n".join(chunks_by_id[sid].text for sid in item["sections"])
        stray = stray_numbers("\n".join(item["answer_points"]), material)
        if stray:
            raise ValueError(f"answer points with numbers not in its sections: {stray}")
    hidden = tuple(sections) if task.kind == "unauthorized" else ()
    if any(visible(task.asker, s.document.department) for s in hidden):
        raise ValueError("the asker can see the document it shouldn't get")
    if task.kind == "unauthorized" and not hidden:
        raise ValueError("doesn't say where the answer it shouldn't get is")
    turns = ()
    if task.kind == "multi_turn":
        if not (item["previous_question"] and item["previous_answer"]):
            raise ValueError("a follow-up without its first turn")
        turns = (Turn(role="user", content=item["previous_question"]), Turn(role="assistant", content=item["previous_answer"]))
    # Without it, 16 of the first 255 candidates asked about "the Home New Product Launch" with three to choose from
    asked = " ".join([*(turn.content for turn in turns), item["question"]])
    own_starts = {c.section.document.campaign_start for c in task.chunks if c.section.document.campaign}
    if task.repeated and not (WHICH_ONE.search(asked) or any(names_the_day(asked, d) for d in own_starts)):
        raise ValueError(f"doesn't say which {' or '.join(name for name, _ in task.repeated)} it means")
    # A month isn't enough when two campaigns of the name start in it: 5 candidates said "the February Beauty Fan
    # Appreciation Day", and two of them start in February 2026
    for start in task.need_day:
        if not names_the_day(asked, start):
            raise ValueError(f"doesn't give the day: another campaign of the name also starts in {MONTHS[start.month - 1]} {start.year}")
    question_id = "q-" + hashlib.sha256(f"{task.id}#{index}".encode()).hexdigest()[:10]
    return Question(
        id=question_id, question=item["question"].strip(), turns=turns, asker=task.asker, route=route, expected=expected,
        relevant=tuple(sections) if expected == "answer" and route != "data" else (),
        hidden=hidden, related=tuple(c.section for c in task.chunks) if task.kind == "near_miss" else (),
        gold_sql=gold_sql, gold_result=gold_result,
        answer_points=tuple(item["answer_points"]) if expected == "answer" else (),
        tags=tuple(dict.fromkeys(tags)), source=f"generated:{MODEL}", group=task.id,
        split=split_for(question_id, task.id, expected, f"generated:{MODEL}"))


def normalized(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def dedupe_key(question: Question) -> str:
    """A follow-up is the same question only after the same conversation: "and the one before that?" was dropped as a
    duplicate of another conversation's follow-up when only the last line was compared."""
    return normalized(" ".join([*(turn.content for turn in question.turns), question.question]))


# ---------------------------------------------------------------------------
# Running it
# ---------------------------------------------------------------------------
def write_task(client: OpenAI, conn, task: Task, seen: set[str], spent: dict[str, int]) -> tuple[list[Question], list[str]]:
    response = client.responses.create(
        model=MODEL, instructions=INSTRUCTIONS, input=task_prompt(conn, task), max_output_tokens=MAX_OUTPUT_TOKENS,
        reasoning={"effort": "low"}, timeout=REQUEST_TIMEOUT_SECONDS,
        text={"format": {"type": "json_schema", "name": "questions", "schema": SCHEMA, "strict": True}})
    spent["input"] += response.usage.input_tokens
    spent["output"] += response.usage.output_tokens
    if response.status != "completed":
        return [], [f"the answer was cut off ({response.status})"]
    chunks_by_id = {f"S{i}": c for i, c in enumerate(task.chunks, 1)}
    try:
        items = json.loads(response.output_text)["questions"]
    except (ValueError, KeyError) as error:
        return [], [f"the answer isn't the JSON asked for: {error}"]
    kept, dropped = [], []
    for index, item in enumerate(items):
        try:
            question = build(conn, task, item, chunks_by_id, index)
        except (ValueError, ValidationError, psycopg.Error) as error:   # a generated SQL can fail in any way
            dropped.append(f"{item.get('question', '')[:70]!r}: {str(error).splitlines()[0][:160]}")
            continue
        if dedupe_key(question) in seen:
            dropped.append(f"{question.question[:70]!r}: a duplicate")
            continue
        seen.add(dedupe_key(question))
        kept.append(question)
    return kept, dropped


def report(questions: list[Question]) -> None:
    def count(key):
        counts: dict[str, int] = {}
        for q in questions:
            for k in key(q):
                counts[k] = counts.get(k, 0) + 1
        return dict(sorted(counts.items()))
    print(f"{len(questions)} candidates")
    print("  route:", count(lambda q: [q.route]))
    print("  expected:", count(lambda q: [q.expected]))
    print("  tags:", count(lambda q: q.tags))
    print("  split:", count(lambda q: [q.split]), " by route:", count(lambda q: [f"{q.route}/{q.split}"]))
    print("  asked by:", count(lambda q: [q.asker.role]))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    which = parser.add_mutually_exclusive_group(required=True)
    which.add_argument("--pilot", action="store_true")
    which.add_argument("--all", action="store_true")
    which.add_argument("--report", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--budget-dollars", type=Decimal, default=Decimal("3"), help="stop before spending more")
    args = parser.parse_args()

    existing = load(OUT) if OUT.exists() else []
    if args.report:
        report(existing)
        return
    with db.connect() as conn:
        starts: dict[str, tuple[date, ...]] = {}
        for name, start in conn.execute("SELECT name, start_date FROM campaigns ORDER BY start_date"):
            starts[name] = starts.get(name, ()) + (start,)
        tasks = plan_tasks(load_corpus(conn), askers(conn), starts)
        done = {q.group for q in existing}
        todo = [t for t in (pilot(tasks) if args.pilot else tasks) if t.id not in done]
        print(f"{len(tasks)} tasks, about {sum(t.count for t in tasks)} questions; {len(todo)} to do now with {MODEL}", flush=True)
        if args.dry_run:
            for t in todo:
                print(f"  {t.kind:15} x{t.count} {t.asker.role}/{t.asker.department}: {t.id[:90]}")
            return
        client = OpenAI(api_key=settings.openai_api_key, base_url=settings.openai_base_url, max_retries=0)
        spent = {"input": 0, "output": 0}
        seen = {dedupe_key(q) for q in existing}
        questions = list(existing)
        for task in todo:
            cost = sum(Decimal(spent[k]) / 1_000_000 * PRICE_PER_MILLION[k] for k in spent)
            if cost >= args.budget_dollars:
                print(f"stopping: ${cost:.2f} spent", flush=True)
                break
            kept, dropped = write_task(client, conn, task, seen, spent)
            questions += kept
            save(OUT, questions)   # after every task, so a failure loses no finished work
            print(f"  {task.kind:15} kept {len(kept)} of {len(kept) + len(dropped)}: {task.id[:70]}", flush=True)
            for reason in dropped:
                print(f"      dropped {reason}", flush=True)
        cost = sum(Decimal(spent[k]) / 1_000_000 * PRICE_PER_MILLION[k] for k in spent)
        print(f"tokens: {spent['input']:,} in, {spent['output']:,} out, about ${cost:.2f} at the {MODEL} price")
        report(questions)


if __name__ == "__main__":
    main()
