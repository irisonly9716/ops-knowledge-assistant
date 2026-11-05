"""Writes the evaluation corpus into sop_docs/ and campaign_docs/: what to write is in scripts/corpus_spec.py.

A model writes the prose and this script writes the numbers. Each document has an author, with a way of
writing, its own sections and its own length (corpus_spec.py), so the corpus reads like the work of many people. Every table and figure in a campaign document comes
from the database (the same seeded mock data SQL questions are answered from), is rendered here, and is put into
the model's text at a placeholder. The model may quote those numbers but not make any up: a draft with a number
that isn't in its facts is rejected and written again. An SOP may only contain the numbers of its brief. So the
answer to every question about the corpus is known in advance and agrees with SQL.

The files are committed, because a model writes different text every time. This script only writes files that
don't exist yet, so running it again changes nothing that is already there.

    cd llm-service
    uv run python -m scripts.generate_corpus --dry-run --pilot   # sizes and prompts, no model call
    uv run python -m scripts.generate_corpus --pilot             # the first six documents, to judge the quality
    uv run python -m scripts.generate_corpus --all               # every document not written yet
    uv run python -m scripts.generate_corpus --report            # authors and paragraph lengths of the corpus

It reads the development database, so run scripts.generate_mock_data first on a new machine.
"""
import argparse
import random
import re
import sys
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP, Decimal
from pathlib import Path

import tiktoken
from openai import OpenAI

from app import db
from app.config import settings
from app.rag.chunking import TOKEN_ENCODING
from scripts.corpus_spec import (ALL, CAMPAIGNS, EXISTING_SOPS, PILOT, PLAN_AUTHORS, REVIEW_AUTHORS, SOP_AUTHORS,
                                 SOP_HOUSE_AUTHORS, SOPS, TEMPLATE_AUTHORS, TEMPLATE_SECTIONS, Author, CampaignSpec,
                                 Section, SopSpec)
from scripts.generate_mock_data import DEPARTMENTS, PRODUCTS_BY_DEPT

REPO_ROOT = Path(__file__).resolve().parents[2]
SOP_DIR = REPO_ROOT / "sop_docs"
CAMPAIGN_DIR = REPO_ROOT / "campaign_docs"

ATTEMPTS = 3                       # a draft that breaks a rule is written again, at most this often
REQUEST_TIMEOUT_SECONDS = 120
MAX_OUTPUT_TOKENS = 2500           # the longest sample document is about 800 tokens
# OpenAI's price for gpt-6-luna, checked 2026-11-05, per million tokens. Only used to print an estimate
PRICE_PER_MILLION = {"input": Decimal("0.10"), "output": Decimal("0.50")}

MONTHS = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()


# ---------------------------------------------------------------------------
# Facts from the database
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Product:
    name: str
    units_sold: int
    sales: Decimal
    profit: Decimal
    margin: Decimal
    return_rate: Decimal


@dataclass(frozen=True)
class CampaignFacts:
    campaign_id: int
    name: str
    department: str
    host: str
    start: date
    end: date
    status: str
    live_days: int
    planned_cost: Decimal
    actual_cost: Decimal | None
    gmv: Decimal | None
    conversion: Decimal | None        # the average of the daily conversion rates
    peak_viewers: int | None
    products: tuple[Product, ...]

    @property
    def calendar_days(self) -> int:
        return (self.end - self.start).days + 1

    @property
    def total_profit(self) -> Decimal:
        return sum((p.profit for p in self.products), Decimal(0))

    @property
    def roi(self) -> Decimal:
        # The project's definition: product profit already nets the cost of goods (AGENTS.md, docs/data-schema.md)
        return self.total_profit / self.actual_cost


def load_campaign(conn, spec: CampaignSpec) -> CampaignFacts:
    row = conn.execute(
        """SELECT c.campaign_id, c.name, d.name, h.name, c.start_date, c.end_date, c.status, c.planned_operating_cost,
                  c.actual_operating_cost, count(m.id), sum(m.gmv), avg(m.conversion_rate), max(m.peak_concurrent_viewers)
           FROM campaigns c
           JOIN departments d USING (department_id)
           JOIN hosts h ON h.host_id = c.host_id
           LEFT JOIN campaign_metrics m ON m.campaign_id = c.campaign_id
           WHERE d.name = %s AND c.name = %s AND c.start_date = %s
           GROUP BY c.campaign_id, d.name, h.name""",
        (spec.department, spec.name, spec.start)).fetchall()
    if len(row) != 1:
        raise LookupError(f"{len(row)} campaigns match {spec.department} / {spec.name} / {spec.start}")
    (campaign_id, name, department, host, start, end, status, planned, actual, days, gmv, conversion, peak), = row
    products = conn.execute(
        """SELECT product_name, units_sold, sales_amount, profit, profit_margin, return_rate FROM campaign_products
           WHERE campaign_id = %s ORDER BY sales_amount DESC""", (campaign_id,)).fetchall()
    return CampaignFacts(campaign_id, name, department, host, start, end, status, days, planned, actual, gmv, conversion,
                         peak, tuple(Product(*p) for p in products))


def planned_lineup(facts: CampaignFacts) -> list[str]:
    """The products the plan names. A campaign still running has no product rows yet: its plan takes products of
    its department. No prices: the mock data's average prices (sales over units) agree with SQL but aren't
    believable, such as instant ramen at $207, and a plan doesn't need them."""
    if facts.products:
        return [p.name for p in facts.products]
    return random.Random(f"lineup-{facts.campaign_id}").sample(PRODUCTS_BY_DEPT[facts.department], 4)


# ---------------------------------------------------------------------------
# Targets: the plan's, which the database doesn't have
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Range:
    low: Decimal
    high: Decimal

    def verdict(self, actual: Decimal) -> str:
        if actual < self.low:
            return "Below target"
        return "Above target" if actual > self.high else "On target"


@dataclass(frozen=True)
class Targets:
    gmv: Range
    conversion: Range
    peak_viewers: Range


def _round(value: Decimal, step: Decimal, rounding) -> Decimal:
    return (value / step).quantize(Decimal(1), rounding=rounding) * step


def targets_for(facts: CampaignFacts) -> Targets:
    """Ranges around what happened, seeded by the campaign, so that some campaigns meet a target and some don't, and
    the plan and the review always agree. A campaign still running is judged by its pace so far."""
    rng = random.Random(f"targets-{facts.campaign_id}")
    gmv = facts.gmv * facts.calendar_days / facts.live_days if facts.status == "running" else facts.gmv

    def scaled(value) -> Decimal:
        return Decimal(value) * Decimal(str(round(rng.uniform(0.8, 1.25), 3)))

    center = scaled(gmv)
    step = Decimal(10_000) if center >= 200_000 else Decimal(5_000)
    gmv_range = Range(_round(center * Decimal("0.85"), step, ROUND_FLOOR), _round(center * Decimal("1.15"), step, ROUND_CEILING))
    conversion_low = max(_round(scaled(facts.conversion) * 100 - 1, Decimal(1), ROUND_FLOOR), Decimal(1)) / 100
    peak_low = max(_round(scaled(facts.peak_viewers) - 1000, Decimal(500), ROUND_FLOOR), Decimal(500))
    return Targets(gmv_range, Range(conversion_low, conversion_low + Decimal("0.02")), Range(peak_low, peak_low + 2000))


# ---------------------------------------------------------------------------
# How numbers are written: the forms the hand-written samples use
# ---------------------------------------------------------------------------
def money_k(value: Decimal) -> str:
    sign = "-" if value < 0 else ""
    value = abs(Decimal(value))
    if value >= 1_000_000:
        return f"{sign}${value / 1_000_000:,.2f}M"
    return f"{sign}${value / 1000:,.1f}K"


def money_whole(value: Decimal) -> str:
    return f"${Decimal(value):,.0f}"


def thousands(value: Decimal) -> str:
    return f"{Decimal(value) / 1000:,.1f}"


def pct(value: Decimal, decimals: int) -> str:
    return f"{Decimal(value) * 100:.{decimals}f}%"


def count(value) -> str:
    return f"{int(value):,}"


def date_text(day: date) -> str:
    return f"{MONTHS[day.month - 1]} {day.day}, {day.year}"


def cost_verdict(planned: Decimal, actual: Decimal) -> str:
    change = (actual - planned) / planned
    if change <= 0:
        return f"{pct(-change, 1)} under budget"
    text = f"{pct(change, 1)} over budget"
    return f"**{text}**, flagged: more than 5% over the plan" if change > Decimal("0.05") else text


# ---------------------------------------------------------------------------
# What goes in each campaign document
# ---------------------------------------------------------------------------
def campaign_title(spec: CampaignSpec, kind: str, repeated_names: set[str]) -> str:
    """A name that repeats is told apart by its start date, the way people refer to such campaigns."""
    name = f"{spec.name} ({date_text(spec.start)})" if spec.name in repeated_names else spec.name
    return f"{name} - {'Campaign Plan' if kind == 'plan' else 'Post-Campaign Review'}"


def campaign_filename(spec: CampaignSpec, kind: str) -> str:
    explicit = spec.plan_file if kind == "plan" else spec.review_file
    return explicit or f"{slug(spec.name)}-{spec.start.isoformat()}-{kind}.md"


PROMOTIONS = ("{n}% off", "buy two, get {n}% off", "a free gift with orders over ${t}", "no discount of its own; part of the spend-and-save tiers")


def promotion(facts: CampaignFacts, product: str) -> str:
    rng = random.Random(f"promotion-{facts.campaign_id}-{product}")
    return rng.choice(PROMOTIONS).format(n=rng.choice((10, 15, 20, 25)), t=rng.choice((40, 60, 80)))


def plan_parts(facts: CampaignFacts, lineup: list[str], targets: Targets) -> tuple[list[str], dict[str, str]]:
    budget = money_whole(_round(facts.planned_cost, Decimal(1000), ROUND_HALF_UP))
    facts_lines = [
        f"Department: {facts.department}",
        f"Campaign: {facts.name}, from {date_text(facts.start)} to {date_text(facts.end)} ({facts.calendar_days} days)",
        f"Host: {facts.host}",
        "Products, with their promotion:",
        *[f"  - {name}: {promotion(facts, name)}" for name in lineup],
        f"Planned operating cost: about {budget}",
        "The targets are in the {{TARGETS}} block",
    ]
    block = "\n".join([
        f"- Expected GMV: {money_whole(targets.gmv.low)}-{money_whole(targets.gmv.high)}",
        f"- Expected conversion rate: {pct(targets.conversion.low, 0)}-{pct(targets.conversion.high, 0)}",
        f"- Expected peak concurrent viewers: {count(targets.peak_viewers.low)}-{count(targets.peak_viewers.high)}",
        f"- Planned operating cost (placement fees + promotion + giveaways): about {budget}",
    ])
    return facts_lines, {"{{TARGETS}}": block}


def review_parts(facts: CampaignFacts, targets: Targets, has_plan: bool) -> tuple[list[str], dict[str, str]]:
    target_header = "Target (see the campaign plan)" if has_plan else "Target (set in planning)"
    gmv_target = f"{money_k_whole(targets.gmv.low)}-{money_k_whole(targets.gmv.high)}"
    conversion_target = f"{pct(targets.conversion.low, 0)}-{pct(targets.conversion.high, 0)}"
    peak_target = f"{count(targets.peak_viewers.low)}-{count(targets.peak_viewers.high)}"
    rows = [
        ("GMV", gmv_target, money_k(facts.gmv), targets.gmv.verdict(facts.gmv)),
        ("Conversion rate", conversion_target, pct(facts.conversion, 2), targets.conversion.verdict(facts.conversion)),
        ("Peak concurrent viewers", peak_target, count(facts.peak_viewers), targets.peak_viewers.verdict(Decimal(facts.peak_viewers))),
        ("Operating cost", f"Planned {money_k(facts.planned_cost)}", f"Actual {money_k(facts.actual_cost)}",
         cost_verdict(facts.planned_cost, facts.actual_cost)),
    ]
    results = "\n".join([f"| Metric | {target_header} | Actual | Verdict |", "|---|---|---|---|",
                         *[f"| {a} | {b} | {c} | {d} |" for a, b, c, d in rows]])
    roi = "\n".join([
        f"- Total product profit over the {len(facts.products)} featured products, product cost already deducted: "
        f"**{money_k(facts.total_profit)}**",
        f"- Actual operating cost: **{money_k(facts.actual_cost)}**",
        f"- **ROI = total product profit / operating cost = {thousands(facts.total_profit)} / {thousands(facts.actual_cost)} "
        f"≈ {facts.roi:.2f}**",
    ])
    products = "\n".join([
        "| Product | Units sold | Sales | Profit | Margin | Return rate |", "|---|---|---|---|---|---|",
        *[f"| {p.name} | {count(p.units_sold)} | {money_k(p.sales)} | {money_k(p.profit)} | {pct(p.margin, 2)} | "
          f"{pct(p.return_rate, 1)} |" for p in facts.products],
    ])
    facts_lines = [
        f"Department: {facts.department}",
        f"Campaign: {facts.name}, from {date_text(facts.start)} to {date_text(facts.end)}, streamed on {facts.live_days} days",
        f"Host: {facts.host}",
        "Results against targets, in the {{RESULTS_TABLE}} block:",
        *[f"  - {a}: target {b}, actual {c}, {d}" for a, b, c, d in rows],
        f"ROI, in the {{{{ROI_BLOCK}}}} block: total product profit {money_k(facts.total_profit)}, actual operating cost "
        f"{money_k(facts.actual_cost)}, ROI {facts.roi:.2f}",
        "Products, in the {{PRODUCT_TABLE}} block:",
        *[f"  - {p.name}: {count(p.units_sold)} units, sales {money_k(p.sales)}, profit {money_k(p.profit)}, margin "
          f"{pct(p.margin, 2)}, return rate {pct(p.return_rate, 1)}" for p in facts.products],
    ]
    return facts_lines, {"{{RESULTS_TABLE}}": results, "{{ROI_BLOCK}}": roi, "{{PRODUCT_TABLE}}": products}


def money_k_whole(value: Decimal) -> str:
    return f"${Decimal(value) / 1000:,.0f}K"


# ---------------------------------------------------------------------------
# The rules a draft must keep
# ---------------------------------------------------------------------------
NUMBER = re.compile(r"(?<![\w.$])-?\$?\d+(?:,\d{3})*(?:\.\d+)?(?:[KM](?![A-Za-z]))?%?")
DATE = re.compile(r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.? \d{1,2}(?:st|nd|rd|th)?(?:, \d{4})?"
                  r"|\b\d{4}-\d{2}-\d{2}\b")
YEAR = re.compile(r"\b20[2-3]\d\b")
SMALL = 10   # whole numbers up to this are counting words ("2 products", "step 3"), not facts


def number_key(token: str) -> str:
    return token.lstrip("-").replace("$", "").replace(",", "")


def numbers_in(text: str) -> set[str]:
    text = YEAR.sub(" ", DATE.sub(" ", text))
    return {number_key(t) for t in NUMBER.findall(text)}


def stray_numbers(draft: str, allowed_text: str) -> list[str]:
    """Numbers in the draft that aren't in the facts it was given."""
    allowed = numbers_in(allowed_text)
    return sorted(n for n in numbers_in(draft)
                  if n not in allowed and not (n.isdigit() and int(n) <= SMALL))


# A list marker needs its space: a paragraph that starts in bold (**...) is prose. Without it, two documents failed
# three times for want of a long paragraph they had
PROSE = re.compile(r"\s*(?:#|[-*+]\s|[>|]|\d+[.)]\s|\{\{)")


LIST_ITEM = re.compile(r"\s*(?:[-*+]|\d+[.)])\s+(.*)")


def list_item_lengths(markdown: str) -> list[int]:
    return [len(re.findall(r"\b\w+\b", m.group(1))) for line in markdown.splitlines() if (m := LIST_ITEM.match(line))]


def paragraph_lengths(markdown: str) -> list[int]:
    """Words in each prose paragraph: not a heading, list, table, quote or placeholder."""
    blocks = [b.strip() for b in re.split(r"\n\s*\n", markdown) if b.strip()]
    return [len(re.findall(r"\b\w+\b", b)) for b in blocks if not PROSE.match(b)]


def check_draft(draft: str, *, title: str, sections: tuple[Section, ...], blocks: dict[str, str], allowed_text: str,
                forbidden_names: list[str], words: tuple[int, int], mixed_paragraphs: bool = False) -> list[str]:
    problems = []
    lines = [line.strip() for line in draft.strip().splitlines()]
    headings = [section.heading for section in sections]
    if not lines or lines[0] != f"# {title}":
        problems.append(f"the first line must be exactly: # {title}")
    found = [line[3:].strip() for line in lines if line.startswith("## ")]
    if found != headings:
        problems.append(f"the sections must be exactly, in this order: {headings}; the draft has {found}")
    for placeholder in blocks:
        if lines.count(placeholder) != 1:
            problems.append(f"{placeholder} must appear exactly once, on a line of its own")
    under, current = {}, None
    for line in lines:
        if line.startswith("## "):
            current = line[3:].strip()
        elif line in blocks:
            under.setdefault(line, current)
    for section in sections:
        for placeholder in section.placeholders:
            if placeholder in under and under[placeholder] != section.heading:
                problems.append(f"{placeholder} belongs under ## {section.heading}")
    # A model asked to write a draft again tends to write the table out as well as keeping its placeholder:
    # in the first batch, a review came out with every table twice
    copied = {line.strip() for block in blocks.values() for line in block.splitlines()} & set(lines)
    if blocks and (copied or any(line.startswith("|") for line in lines)):
        problems.append("don't write any table, or any line of a block, yourself: the placeholder is replaced by it")
    stray = stray_numbers(draft, allowed_text)
    if stray:
        problems.append(f"these numbers are not in the facts, so they can't be used: {', '.join(stray)}")
    for name in forbidden_names:
        if name in draft:
            problems.append(f"don't mention {name}: the document belongs to another department")
    n = len(re.findall(r"\b\w+\b", draft))
    if not words[0] <= n <= words[1]:
        problems.append(f"it has {n} words; write {words[0]} to {words[1]}")
    if mixed_paragraphs:
        # Long means a prose paragraph; short can also be a one-line list item, which on the page is as small a piece.
        # A document on the fixed company template has little room for a separate short paragraph: one failed nine
        # drafts in a row asking for both as paragraphs
        lengths = paragraph_lengths(draft)
        long_enough = bool(lengths) and max(lengths) >= 100
        short_enough = min(lengths + list_item_lengths(draft), default=999) <= 20
        if not (long_enough and short_enough):
            # Saying what was counted matters: told only the rule, a model kept failing it, not knowing that its long
            # numbered steps don't count as paragraphs
            counted = ", ".join(f"{n} words" for n in lengths) or "none"
            problems.append(f"mix paragraph lengths: write at least one prose paragraph of 100 words or more, and at "
                            f"least one paragraph or list item of 20 words or fewer. Your prose paragraphs have "
                            f"{counted}; headings, lists, numbered steps and tables don't count as paragraphs")
    return problems


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------
INSTRUCTIONS = """You write internal documents for the operations staff of a US company that sells through livestreams.
Write plain, professional English, the way an experienced operations colleague writes for the team: specific, short
sentences, no marketing language, no filler.
Return only the Markdown document. No frontmatter, no code fences, no comments before or after it.
Rules you must keep:
- Use only the numbers given to you, written exactly as given. Don't calculate, round or invent any other number.
  Counting words such as "two products" are fine.
- Keep every placeholder line such as {{TARGETS}} exactly as given, on a line of its own: a table goes there.
- Use exactly the level-2 (##) headings you are given, in that order, and no others.
- Write the way the author you are given writes, not the way every other document does."""


def body_of(path: Path) -> str:
    return re.sub(r"\A---\n.*?\n---\n", "", path.read_text(encoding="utf-8"), flags=re.DOTALL).strip()


def structure_text(title: str, sections: tuple[Section, ...]) -> str:
    lines = [f"# {title}"]
    for section in sections:
        lines.append(f"## {section.heading}" + (f"   ({section.guidance})" if section.guidance else ""))
        lines += section.placeholders
    return "\n".join(lines)


def sop_sections(spec: SopSpec, author: Author) -> tuple[Section, ...]:
    return TEMPLATE_SECTIONS if spec.like else author.sections


def sop_prompt(spec: SopSpec, author: Author) -> str:
    audience = "every department (it applies company-wide)" if spec.department == ALL else f"the {spec.department} department"
    points = "\n".join(f"- {point}" for point in spec.brief)
    if spec.like:
        example = (f"Every department has a document like this one, on the same company template. Keep the template's "
                   f"sections but write it your way, and take every fact and number from the points above, never from "
                   f"the template:\n<template>\n{body_of(SOP_DIR / spec.like)}\n</template>")
    elif author.example:
        example = (f"An example of your style, on another topic. Don't reuse its content or its numbers:\n"
                   f"<example>\n{body_of(SOP_DIR / 'account-restriction-handling.md')}\n</example>")
    else:
        example = ""
    return f"""Write the standard operating procedure "{spec.title}" for {audience}.
Owner: {spec.responsible_team}. Category: {spec.category}.

It must state every one of these points. You may add details that contain no numbers:
{points}

How you write: {author.style}

Structure, in this order:
{structure_text(spec.title, sop_sections(spec, author))}

Length: {author.words[0]} to {author.words[1]} words.

{example}""".strip()


def campaign_prompt(kind: str, title: str, facts_lines: list[str], author: Author) -> str:
    facts_text = "\n".join(f"- {line}" if not line.startswith("  ") else line for line in facts_lines)
    note = ("It is written before the campaign starts, so it knows nothing about the results." if kind == "plan"
            else "It is written after the campaign, from its results.")
    example = ""
    if author.example:
        example = (f"\n\nAn example of your style, for another campaign. Don't reuse its content or its numbers:\n"
                   f"<example>\n{example_plan() if kind == 'plan' else example_review()}\n</example>")
    return f"""Write "{title}". {note}

Facts:
{facts_text}

How you write: {author.style}

Structure, in this order. The lines in braces are placeholders: copy each one exactly, once, where it is shown, and
don't write out the table or list it stands for:
{structure_text(title, author.sections)}

Length: {author.words[0]} to {author.words[1]} words, not counting what the placeholders stand for.{example}"""


def sections(path: Path) -> dict[str, str]:
    """The body of a Markdown document by its level-2 headings, and its H1 under the key ""."""
    parts, key = {"": []}, ""
    for line in body_of(path).splitlines():
        if line.startswith("## "):
            key = line[3:].strip()
            parts[key] = []
        else:
            parts[key].append(line)
    return {k: "\n".join(v).strip() for k, v in parts.items()}


def without_tables(text: str) -> str:
    return "\n".join(line for line in text.splitlines() if not line.startswith("|")).strip()


def example_plan() -> str:
    """The hand-written plan as a draft should look: the targets are a placeholder. In the first batch, a model
    shown the sample with its tables kept writing the tables out instead of the placeholders."""
    s = sections(CAMPAIGN_DIR / "beauty-black-friday-live-plan.md")
    return "\n\n".join([s[""], "## Background", s["Background"], "## Product Lineup", s["Product Lineup"],
                         "## Pricing and Promotions", s["Pricing and Promotions"],
                         "## Targets (for the post-campaign review)", "{{TARGETS}}", "## Risks", s["Risks"]])


def example_review() -> str:
    """The hand-written review as a draft should look. Its ROI section, which tells how that campaign's formula was
    corrected, becomes the block and one sentence."""
    s = sections(CAMPAIGN_DIR / "beauty-black-friday-live-review.md")
    roi = "Every $1 of marketing spend brought back about $2.29 of product profit."
    recommendations = s["Recommendations for Similar Campaigns"]
    return "\n\n".join([s[""], "## Results vs Targets", "{{RESULTS_TABLE}}", "## ROI", "{{ROI_BLOCK}}", roi,
                         "## Product Breakdown", "{{PRODUCT_TABLE}}", without_tables(s["Product Breakdown"]),
                         "## Recommendations for Similar Campaigns", recommendations])


# ---------------------------------------------------------------------------
# Jobs
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Job:
    path: Path
    title: str
    author: str
    frontmatter: dict[str, str]
    prompt: str
    sections: tuple[Section, ...]
    blocks: dict[str, str]        # placeholder -> what replaces it
    allowed_text: str              # the only numbers the draft may use
    forbidden_names: list[str]
    words: tuple[int, int]
    mixed_paragraphs: bool


def other_departments(department: str) -> list[str]:
    return [] if department == ALL else [d for d in DEPARTMENTS if d != department]


def sop_author(spec: SopSpec) -> Author:
    """Mostly one of the department's two regular writers, sometimes someone else. Seeded by the file, so a
    document keeps its author."""
    rng = random.Random(f"author-{spec.filename}")
    candidates = TEMPLATE_AUTHORS if spec.like else tuple(SOP_AUTHORS)
    house = [name for name in SOP_HOUSE_AUTHORS[spec.department] if name in candidates]
    return SOP_AUTHORS[rng.choice(house) if house and rng.random() < 0.75 else rng.choice(candidates)]


def campaign_author(spec: CampaignSpec, kind: str) -> Author:
    """A plan and the review of the same campaign are often written by different people."""
    authors = PLAN_AUTHORS if kind == "plan" else REVIEW_AUTHORS
    return authors[random.Random(f"author-{campaign_filename(spec, kind)}").choice(sorted(authors))]


def sop_job(spec: SopSpec) -> Job:
    author = sop_author(spec)
    return Job(
        path=SOP_DIR / spec.filename, title=spec.title, author=author.name,
        frontmatter={"title": spec.title, "department": spec.department, "category": spec.category,
                     "responsible_team": spec.responsible_team},
        prompt=sop_prompt(spec, author), sections=sop_sections(spec, author), blocks={},
        allowed_text="\n".join(spec.brief), forbidden_names=other_departments(spec.department), words=author.words,
        mixed_paragraphs=author.mixed_paragraphs)


def campaign_jobs(conn) -> list[Job]:
    names = [spec.name for spec in CAMPAIGNS]
    repeated = {row[0] for row in conn.execute("SELECT name FROM campaigns GROUP BY name HAVING count(*) > 1")}
    repeated |= {name for name in names if names.count(name) > 1}
    jobs = []
    for spec in CAMPAIGNS:
        facts = load_campaign(conn, spec)
        targets = targets_for(facts)
        for kind, wanted in (("plan", spec.plan), ("review", spec.review)):
            if not wanted:
                continue
            title = campaign_title(spec, kind, repeated)
            author = campaign_author(spec, kind)
            written_by = "hand" if (spec.plan_file if kind == "plan" else spec.review_file) else author.name
            if kind == "plan":
                facts_lines, blocks = plan_parts(facts, planned_lineup(facts), targets)
            else:
                facts_lines, blocks = review_parts(facts, targets, spec.plan)
            jobs.append(Job(
                path=CAMPAIGN_DIR / campaign_filename(spec, kind), title=title, author=written_by,
                frontmatter={"doc_type": f"campaign_{kind}", "title": title, "department": facts.department,
                             "campaign_name": facts.name, "campaign_start": facts.start.isoformat(), "host": facts.host},
                prompt=campaign_prompt(kind, title, facts_lines, author), sections=author.sections, blocks=blocks,
                allowed_text="\n".join(facts_lines + list(blocks.values())),
                forbidden_names=other_departments(facts.department), words=author.words,
                mixed_paragraphs=author.mixed_paragraphs))
    return jobs


def render(job: Job, draft: str) -> str:
    body = draft.strip()
    for placeholder, block in job.blocks.items():
        body = body.replace(placeholder, f"\n{block}\n")
    frontmatter = "\n".join(f"{key}: {value}" for key, value in job.frontmatter.items())
    return f"---\n{frontmatter}\n---\n\n{tidy(body)}\n"


def tidy(markdown: str) -> str:
    """A blank line around every heading and block, and never two in a row, the way the hand-written samples look."""
    lines = []
    for line in markdown.splitlines():
        heading = line.startswith("#")
        if heading and lines and lines[-1] != "":
            lines.append("")
        lines.append(line.rstrip())
        if heading:
            lines.append("")
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


# ---------------------------------------------------------------------------
# Running it
# ---------------------------------------------------------------------------
def write_one(client: OpenAI, job: Job, spent: dict[str, int]) -> list[str]:
    """Writes the file and returns no problems, or returns the problems of the last draft."""
    prompt, problems = job.prompt, []
    for attempt in range(1, ATTEMPTS + 1):
        response = client.responses.create(model=settings.chat_model, instructions=INSTRUCTIONS, input=prompt,
                                           max_output_tokens=MAX_OUTPUT_TOKENS, reasoning={"effort": "none"},
                                           timeout=REQUEST_TIMEOUT_SECONDS)
        spent["input"] += response.usage.input_tokens
        spent["output"] += response.usage.output_tokens
        draft = response.output_text
        problems = (["the answer was cut off"] if response.status != "completed" else []) + check_draft(
            draft, title=job.title, sections=job.sections, blocks=job.blocks, allowed_text=job.allowed_text,
            forbidden_names=job.forbidden_names, words=job.words, mixed_paragraphs=job.mixed_paragraphs)
        if not problems:
            job.path.write_text(render(job, draft), encoding="utf-8", newline="\n")
            print(f"  wrote {job.path.relative_to(REPO_ROOT)} by {job.author} (attempt {attempt})", flush=True)
            return []
        print(f"  attempt {attempt} of {job.path.name} broke a rule: {'; '.join(problems)}", flush=True)
        prompt = (f"{job.prompt}\n\nYour previous draft broke these rules:\n" + "\n".join(f"- {p}" for p in problems)
                  + "\nWrite the whole document again and keep every rule.")
    return problems


def all_jobs() -> list[Job]:
    with db.connect() as conn:
        return [sop_job(spec) for spec in SOPS] + campaign_jobs(conn)


def report(paths: list[Path]) -> None:
    """How varied the documents are: the spread the first batch didn't have."""
    per_document = [paragraph_lengths(body_of(path)) for path in paths if path.exists()]
    lengths = sorted(n for doc in per_document for n in doc)
    if not lengths:
        return
    print(f"{len(per_document)} documents, {len(lengths)} prose paragraphs: words per paragraph shortest {lengths[0]}, "
          f"median {lengths[len(lengths) // 2]}, longest {lengths[-1]}; "
          f"{sum(1 for doc in per_document if doc and max(doc) >= 100)} documents have a paragraph of 100 words or more, "
          f"{sum(1 for doc in per_document if doc and min(doc) <= 20)} one of 20 or fewer")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    which = parser.add_mutually_exclusive_group(required=True)
    which.add_argument("--pilot", action="store_true", help="only the documents in corpus_spec.PILOT")
    which.add_argument("--all", action="store_true", help="every document not written yet")
    which.add_argument("--report", action="store_true", help="authors and paragraph lengths of what is written")
    parser.add_argument("--dry-run", action="store_true", help="show what would be written and the prompt sizes")
    parser.add_argument("--budget-tokens", type=int, default=400_000, help="stop before spending more than this")
    args = parser.parse_args()

    if args.report:
        jobs = all_jobs()
        authors: dict[str, int] = {}
        for job in jobs:
            if job.path.exists():
                key = f"{'sop' if job.path.parent == SOP_DIR else job.frontmatter['doc_type']} by {job.author}"
                authors[key] = authors.get(key, 0) + 1
        authors["sop by hand"] = len(EXISTING_SOPS)
        print("written so far:", dict(sorted(authors.items())))
        report([job.path for job in jobs] + [SOP_DIR / name for name in EXISTING_SOPS])
        return
    jobs = [job for job in all_jobs() if not job.path.exists() and (args.all or job.path.name in PILOT)]
    encoding = tiktoken.get_encoding(TOKEN_ENCODING)   # already cached for chunking; close enough for an estimate
    prompt_tokens = sum(len(encoding.encode(INSTRUCTIONS + job.prompt)) for job in jobs)
    print(f"{len(jobs)} documents to write with {settings.chat_model}, about {prompt_tokens:,} prompt tokens "
          f"({len(SOPS) + len(EXISTING_SOPS)} SOPs and {sum(s.plan + s.review for s in CAMPAIGNS)} campaign documents "
          f"in the whole corpus)", flush=True)
    if args.dry_run:
        for job in jobs:
            print(f"  {job.path.relative_to(REPO_ROOT)}  {len(encoding.encode(job.prompt)):,} tokens, by {job.author}: {job.title}")
        return

    client = OpenAI(api_key=settings.openai_api_key, base_url=settings.openai_base_url, max_retries=0)
    spent = {"input": 0, "output": 0}
    failed = []
    for job in jobs:
        if spent["input"] + spent["output"] >= args.budget_tokens:
            print(f"stopping: {args.budget_tokens:,} tokens spent", flush=True)
            break
        problems = write_one(client, job, spent)
        if problems:
            failed.append((job.path.name, problems))
    cost = sum(Decimal(spent[k]) / 1_000_000 * PRICE_PER_MILLION[k] for k in spent)
    print(f"tokens: {spent['input']:,} in, {spent['output']:,} out, about ${cost:.3f} at the {settings.chat_model} price")
    report([job.path for job in jobs])
    for name, problems in failed:
        print(f"FAILED {name}: {'; '.join(problems)}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
