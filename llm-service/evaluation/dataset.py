"""The evaluation dataset: one question per line of eval/*.jsonl, and how it names what a right answer uses.

A question names its documents by what they are (an SOP by department and title, a plan or review by its campaign's
department, name and start date) and its sections by heading path, never by doc_id or chunk_id: those change when the
corpus is ingested again or the chunking changes, and every label would point at the wrong text. resolve() turns a
section into today's chunk ids.
"""
import hashlib
import json
from collections.abc import Iterable
from datetime import date
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

DocType = Literal["sop", "campaign_plan", "campaign_review"]
Role = Literal["staff", "manager", "executive", "admin"]
Route = Literal["documents", "data", "both", "none"]
Tag = Literal[
    "direct", "keyword", "paraphrase", "colloquial", "vague",            # how it is asked
    "cross_document", "near_miss", "unauthorized", "out_of_scope",      # what makes it hard
    "general_knowledge",     # out of scope, but public knowledge: what a router for general questions would answer
    "multi_turn", "planning_advice",
    "single_table", "multi_table", "time_range",                        # what its SQL needs
]

DEV_SHARE = 0.4


class Record(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class DocumentRef(Record):
    doc_type: DocType
    department: str
    title: str | None = None            # an SOP: department + title is its natural key
    campaign: str | None = None         # a plan or review: its campaign, told apart by the start date
    campaign_start: date | None = None

    @model_validator(mode="after")
    def names_one_document(self):
        if self.doc_type == "sop" and not self.title:
            raise ValueError("an SOP is named by its title")
        if self.doc_type != "sop" and not (self.campaign and self.campaign_start):
            raise ValueError("a plan or review is named by its campaign and start date")
        return self


class Section(Record):
    document: DocumentRef
    heading_path: tuple[str, ...] = ()   # empty: the whole document


class Turn(Record):
    role: Literal["user", "assistant"]
    content: str


class Asker(Record):
    department: str | None
    role: Role


class Question(Record):
    id: str
    question: str = Field(min_length=5, max_length=400)
    turns: tuple[Turn, ...] = ()               # the conversation before this question, for a follow-up
    asker: Asker
    route: Route
    expected: Literal["answer", "refuse"]
    relevant: tuple[Section, ...] = ()        # what a right answer cites
    hidden: tuple[Section, ...] = ()          # what the asker may not see and must not get
    related: tuple[Section, ...] = ()         # close to the question but without its answer
    gold_sql: str | None = None
    gold_result: tuple[tuple[str | None, ...], ...] | None = None   # every value as text, so Decimals stay exact
    answer_points: tuple[str, ...] = ()       # what a right answer has to say
    tags: tuple[Tag, ...]
    source: str
    group: str                                # questions from one source: they share a split
    split: Literal["dev", "test"]
    status: Literal["candidate", "accepted", "rejected"] = "candidate"
    note: str | None = None                   # the reviewer's reason for a change or a rejection

    @model_validator(mode="after")
    def consistent(self):
        if self.expected == "answer" and not self.answer_points:
            raise ValueError("a question to answer needs the points of its answer")
        if self.route in ("data", "both") and self.expected == "answer" and not self.gold_sql:
            raise ValueError(f"route={self.route} needs a gold SQL")
        if self.route in ("documents", "both") and self.expected == "answer" and not self.relevant:
            raise ValueError(f"route={self.route} needs the sections a right answer cites")
        if any(visible(self.asker, section.document.department) for section in self.hidden):
            raise ValueError("a hidden section must be one the asker can't see")
        if self.split != split_for(self.id, self.group, self.expected, self.source):
            raise ValueError("the split doesn't follow the rule in split_for")
        return self


def split_of(key: str) -> str:
    """Stable for a key, whatever else is added."""
    value = int(hashlib.sha256(key.encode("utf-8")).hexdigest()[:8], 16) / 2 ** 32
    return "dev" if value < DEV_SHARE else "test"


def split_for(question_id: str, group: str, expected: str, source: str) -> str:
    """A question to answer goes with its group: questions written from the same section or about the same SQL land
    on the same side, or a reworded question in test would have its answer shown in dev. A refusal has no answer to
    show, so each is split on its own: by group, the 39 refusals came from 12 groups and 32 of them landed in test,
    leaving dev too few to calibrate the refusal threshold on. A refusal written by hand goes to dev for the same
    reason."""
    if expected == "refuse":
        return "dev" if source == "human" else split_of(question_id)
    return split_of(group)


def load(path: Path) -> list[Question]:
    with path.open(encoding="utf-8") as lines:
        return [Question.model_validate_json(line) for line in lines if line.strip()]


def save(path: Path, questions: Iterable[Question]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as out:
        for question in questions:
            out.write(json.dumps(question.model_dump(mode="json", exclude_defaults=True), ensure_ascii=False) + "\n")


def visible(asker: Asker, department: str) -> bool:
    """The same rule as app.rag.retrieval.visible_departments, for a document's department."""
    return asker.role in ("executive", "admin") or department in (asker.department, "ALL")


def resolve(conn, section: Section) -> list[int]:
    """Today's chunk ids of a section: of the whole document when it names no heading path."""
    ref = section.document
    rows = conn.execute(
        """SELECT ch.chunk_id FROM doc_chunks ch
           JOIN documents d USING (doc_id)
           LEFT JOIN campaigns c ON c.campaign_id = d.campaign_id
           WHERE d.doc_type = %(doc_type)s AND d.department = %(department)s
             AND (%(doc_type)s <> 'sop' OR lower(d.title) = lower(%(title)s))
             AND (%(doc_type)s = 'sop' OR (c.name = %(campaign)s AND c.start_date = %(start)s))
             AND (%(path)s::jsonb = '[]'::jsonb OR ch.metadata->'heading_path' = %(path)s::jsonb)
           ORDER BY ch.chunk_index""",
        {"doc_type": ref.doc_type, "department": ref.department, "title": ref.title, "campaign": ref.campaign,
         "start": ref.campaign_start, "path": json.dumps(list(section.heading_path))}).fetchall()
    return [row[0] for row in rows]
