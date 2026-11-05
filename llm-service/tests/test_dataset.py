"""The evaluation dataset: what a question must say, the split, and finding a labelled section in the database
(a throwaway one)."""
import json
import uuid
from datetime import date

import pytest
from postgres_helpers import postgres_available, throwaway_database
from pydantic import ValidationError

from app import db
from app.rag.embeddings import EMBEDDING_DIMENSIONS
from app.rag.store import vector_literal
from evaluation.dataset import Asker, DocumentRef, Question, Section, load, resolve, save, split_of, visible

SOP = DocumentRef(doc_type="sop", department="ALL", title="Refund and Return Requests")
REVIEW = DocumentRef(doc_type="campaign_review", department="Home Goods", campaign="Home New Product Launch",
                     campaign_start=date(2026, 1, 8))
STAFF = Asker(department="Beauty", role="staff")


def question(**overrides) -> Question:
    fields = dict(id="q-1", question="Do we refund the list price?", asker=STAFF, route="documents", expected="answer",
                  relevant=(Section(document=SOP, heading_path=("Refund and Return Requests", "Procedure")),),
                  answer_points=("the price the customer paid",), tags=("paraphrase",), source="test", group="g1",
                  split=split_of("g1"))
    return Question(**{**fields, **overrides})


def test_a_question_survives_the_file(tmp_path):
    path = tmp_path / "q.jsonl"
    original = [question(), question(id="q-2", route="data", relevant=(), gold_sql="SELECT 1", gold_result=(("1",),))]
    save(path, original)
    assert load(path) == original


def test_the_file_has_one_question_per_line_and_leaves_out_defaults(tmp_path):
    path = tmp_path / "q.jsonl"
    save(path, [question(), question(id="q-2")])
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert "turns" not in json.loads(lines[0])


def test_a_question_to_answer_needs_its_answer_points():
    with pytest.raises(ValidationError, match="points"):
        question(answer_points=())


def test_a_refusal_needs_no_answer_points():
    assert question(expected="refuse", answer_points=(), relevant=()).expected == "refuse"


def test_a_question_about_numbers_needs_its_sql():
    with pytest.raises(ValidationError, match="gold SQL"):
        question(route="data", relevant=())


def test_a_question_about_documents_needs_the_sections_it_cites():
    with pytest.raises(ValidationError, match="sections"):
        question(relevant=())


def test_an_unauthorized_question_names_what_the_asker_may_not_see():
    with pytest.raises(ValidationError, match="may not see"):
        question(tags=("unauthorized",), expected="refuse", answer_points=(), relevant=())


def test_the_split_follows_the_group():
    with pytest.raises(ValidationError, match="split"):
        question(split="test" if split_of("g1") == "dev" else "dev")


def test_about_40_percent_of_groups_go_to_dev_always_the_same_ones():
    splits = [split_of(f"group {i}") for i in range(2000)]
    assert 0.37 < splits.count("dev") / len(splits) < 0.43
    assert splits == [split_of(f"group {i}") for i in range(2000)]


def test_a_document_is_named_by_what_it_is_not_by_its_id():
    with pytest.raises(ValidationError, match="title"):
        DocumentRef(doc_type="sop", department="ALL")
    with pytest.raises(ValidationError, match="campaign"):
        DocumentRef(doc_type="campaign_plan", department="Beauty", campaign="Beauty Summer Sale")


@pytest.mark.parametrize("asker, department, seen", [
    (STAFF, "Beauty", True), (STAFF, "ALL", True), (STAFF, "Home Goods", False),
    (Asker(department=None, role="executive"), "Home Goods", True),
])
def test_who_can_see_a_document(asker, department, seen):
    assert visible(asker, department) is seen


def add_document(conn, doc_type: str, department: str, title: str | None, campaign_id: int | None,
                 paths: list[list[str]]) -> None:
    upload_id = str(uuid.uuid4())
    conn.execute("""INSERT INTO uploads (upload_id, source, uploaded_by, content_kind, doc_type, department, campaign_id,
                                         title, original_filename, storage_key, status)
                    VALUES (%s, 'manual', 1, 'document', %s, %s, %s, %s, 'x.md', %s, 'done')""",
                 (upload_id, doc_type, department, campaign_id, title, upload_id))
    doc_id = conn.execute("""INSERT INTO documents (doc_type, title, department, campaign_id, upload_id)
                             VALUES (%s, %s, %s, %s, %s) RETURNING doc_id""",
                          (doc_type, title or "generated", department, campaign_id, upload_id)).fetchone()[0]
    for index, path in enumerate(paths):
        conn.execute("""INSERT INTO doc_chunks (doc_id, department, chunk_index, content, embedding, embedding_model, metadata)
                        VALUES (%s, %s, %s, 'text', %s, 'm', %s)""",
                     (doc_id, department, index, vector_literal([0.1] * EMBEDDING_DIMENSIONS), json.dumps({"heading_path": path})))


@pytest.mark.skipif(not postgres_available(), reason="needs the docker compose Postgres")
def test_a_section_is_found_by_its_document_and_heading_path_whatever_its_ids():
    with throwaway_database() as name, db.connect(name) as conn:
        conn.execute("INSERT INTO departments (name) VALUES ('Home Goods')")
        conn.execute("INSERT INTO users (name, department_id, role) VALUES ('Test Staff', 1, 'staff')")
        conn.execute("INSERT INTO hosts (name, department_id) VALUES ('Lisa Hernandez', 1)")
        conn.execute("""INSERT INTO campaigns (name, department_id, host_id, start_date, end_date, status) VALUES
                        ('Home New Product Launch', 1, 1, '2026-01-08', '2026-02-16', 'finished'),
                        ('Home New Product Launch', 1, 1, '2026-01-18', '2026-02-05', 'finished')""")
        add_document(conn, "sop", "ALL", "Refund and Return Requests", None,
                     [["Refund and Return Requests", "When to Use"], ["Refund and Return Requests", "Procedure"]])
        add_document(conn, "campaign_review", "Home Goods", None, 1,
                     [["Home New Product Launch (Jan 8, 2026) - Post-Campaign Review", "Results"]])
        add_document(conn, "campaign_review", "Home Goods", None, 2,
                     [["Home New Product Launch (Jan 18, 2026) - Post-Campaign Review", "Results"]])
        procedure = resolve(conn, Section(document=SOP, heading_path=("Refund and Return Requests", "Procedure")))
        whole = resolve(conn, Section(document=DocumentRef(doc_type="sop", department="ALL", title="refund and return requests")))
        second_launch = resolve(conn, Section(document=REVIEW.model_copy(update={"campaign_start": date(2026, 1, 18)})))
        missing = resolve(conn, Section(document=SOP, heading_path=("Refund and Return Requests", "Appeals")))
    assert len(procedure) == 1 and len(whole) == 2 and procedure[0] in whole
    assert len(second_launch) == 1 and second_launch[0] not in whole
    assert missing == []
