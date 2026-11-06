"""The review page's API, with a throwaway database: what a reviewer can change, what is checked, what is exported."""
import json
from datetime import date

import pytest
from fastapi.testclient import TestClient
from postgres_helpers import postgres_available, throwaway_database
from test_dataset import add_document

from app import db
from evaluation.dataset import Asker, DocumentRef, Question, Section, load, save, split_of
from evaluation.review import create_app

pytestmark = pytest.mark.skipif(not postgres_available(), reason="needs the docker compose Postgres")

SOP = DocumentRef(doc_type="sop", department="ALL", title="Refund and Return Requests")
PROCEDURE = Section(document=SOP, heading_path=("Refund and Return Requests", "Procedure"))
SQL = "SELECT sum(m.gmv) FROM campaign_metrics m JOIN campaigns c USING (campaign_id) JOIN departments d USING (department_id) WHERE d.name = 'Beauty'"


@pytest.fixture(scope="module")
def database():
    with throwaway_database() as name:
        with db.connect(name) as conn:
            conn.execute("INSERT INTO departments (name) VALUES ('Beauty')")
            conn.execute("INSERT INTO users (name, department_id, role) VALUES ('Test Staff', 1, 'staff')")
            conn.execute("INSERT INTO hosts (name, department_id) VALUES ('Tommy Walter', 1)")
            conn.execute("""INSERT INTO campaigns (name, department_id, host_id, start_date, end_date, status)
                            VALUES ('Beauty Summer Sale', 1, 1, '2026-06-12', '2026-07-21', 'finished')""")
            conn.execute("INSERT INTO campaign_metrics (campaign_id, date, gmv) VALUES (1, '2026-06-12', 1000.50)")
            add_document(conn, "sop", "ALL", "Refund and Return Requests", None,
                         [["Refund and Return Requests", "When to Use"], ["Refund and Return Requests", "Procedure"]])
        yield name


def question(qid, group, **overrides) -> Question:
    fields = dict(id=qid, question="Do we refund the list price?", asker=Asker(department="Beauty", role="staff"),
                  route="documents", expected="answer", relevant=(PROCEDURE,), answer_points=("the price paid",),
                  tags=("paraphrase",), source="generated:test", group=group, split=split_of(group))
    return Question(**{**fields, **overrides})


@pytest.fixture
def setup(tmp_path, database):
    candidates = tmp_path / "candidates.jsonl"
    save(candidates, [question("q-1", "g1"),
                      question("q-2", "g2", route="data", relevant=(), gold_sql=SQL, gold_result=(("1000.50",),), tags=("direct",)),
                      question("q-3", "g4")])   # q-3 alone lands in dev, its group g4 in test
    client = TestClient(create_app(candidates, tmp_path, connect=lambda: db.connect(database)))
    return client, candidates, tmp_path


def test_the_page_and_every_question_are_served(setup):
    client, _, _ = setup
    assert "Question review" in client.get("/").text
    assert [q["id"] for q in client.get("/api/questions").json()] == ["q-1", "q-2", "q-3"]


def test_the_corpus_comes_by_document_with_its_sections(setup):
    client, _, _ = setup
    [document] = client.get("/api/corpus").json()
    assert document["ref"]["title"] == "Refund and Return Requests"
    assert [s["heading_path"][1] for s in document["sections"]] == ["When to Use", "Procedure"]


def test_accepting_a_question_is_written_to_the_file_at_once(setup):
    client, candidates, _ = setup
    q = client.get("/api/questions").json()[0]
    assert client.put("/api/questions/q-1", json={**q, "status": "accepted", "note": "fine"}).status_code == 200
    stored = {x.id: x for x in load(candidates)}["q-1"]
    assert (stored.status, stored.note) == ("accepted", "fine")


def test_an_edit_that_breaks_a_rule_is_refused_and_nothing_is_written(setup):
    client, candidates, _ = setup
    before = candidates.read_text(encoding="utf-8")
    response = client.put("/api/questions/q-1", json={"answer_points": []})
    assert response.status_code == 400 and "points" in response.json()["error"]
    assert candidates.read_text(encoding="utf-8") == before


def test_where_a_question_came_from_can_not_be_changed(setup):
    client, candidates, _ = setup
    other = "test" if split_of("g1") == "dev" else "dev"
    response = client.put("/api/questions/q-1", json={"id": "x", "source": "human", "group": "g9", "split": other,
                                                      "note": "tried to move it"})
    assert response.status_code == 200   # the edit is saved; only where it came from is kept as it was
    stored = {x.id: x for x in load(candidates)}["q-1"]
    assert (stored.source, stored.group, stored.split, stored.note) == ("generated:test", "g1", split_of("g1"), "tried to move it")


def test_an_edited_sql_runs_again_and_a_typed_in_result_is_ignored(setup):
    client, candidates, _ = setup
    q = client.get("/api/questions").json()[1]
    new_sql = SQL.replace("sum(m.gmv)", "count(*)")
    saved = client.put("/api/questions/q-2", json={**q, "gold_sql": new_sql, "gold_result": [["999"]]}).json()
    assert saved["gold_result"] == [["1"]]
    unchanged = client.put("/api/questions/q-2", json={**saved, "gold_result": [["999"]]}).json()
    assert unchanged["gold_result"] == [["1"]]


def test_sql_that_fails_is_refused_with_the_reason(setup):
    client, _, _ = setup
    q = client.get("/api/questions").json()[1]
    response = client.put("/api/questions/q-2", json={**q, "gold_sql": "SELECT nope FROM campaigns"})
    assert response.status_code == 400 and response.json()["error"].startswith("the SQL failed")


def test_the_sql_box_can_not_write(setup):
    client, _, _ = setup
    response = client.post("/api/sql", json={"sql": "WITH x AS (DELETE FROM campaigns RETURNING 1) SELECT * FROM x"})
    assert response.status_code == 400
    assert client.post("/api/sql", json={"sql": "SELECT count(*) FROM campaigns"}).json() == {"rows": [["1"]]}


def test_a_question_written_by_hand_gets_its_own_group_and_split(setup):
    client, candidates, _ = setup
    body = {"question": "whats the refund rule for live deals", "asker": {"department": "Beauty", "role": "staff"},
            "route": "documents", "expected": "answer", "relevant": [json.loads(PROCEDURE.model_dump_json())],
            "answer_points": ["the price paid"], "tags": ["colloquial"]}
    created = client.post("/api/questions", json=body).json()
    assert created["id"].startswith("h-") and created["source"] == "human" and created["status"] == "accepted"
    assert created["group"] == f"human:{created['id']}" and created["split"] == split_of(created["group"])
    assert created["id"] in {q.id for q in load(candidates)}


def test_a_refusal_written_by_hand_goes_to_dev(setup):
    client, _, _ = setup
    body = {"question": "compare our growth with fresh grocery's", "asker": {"department": "Beauty", "role": "staff"},
            "route": "data", "expected": "refuse", "tags": ["unauthorized"]}
    assert client.post("/api/questions", json=body).json()["split"] == "dev"


def test_turning_a_question_into_a_refusal_moves_it_to_the_refusals_split(setup):
    client, _, _ = setup
    q = client.get("/api/questions").json()[2]
    assert split_of("q-3") != split_of("g4") == q["split"]   # otherwise the test couldn't tell the two rules apart
    saved = client.put("/api/questions/q-3", json={**q, "expected": "refuse", "answer_points": [], "relevant": []}).json()
    assert saved["split"] == split_of("q-3")


def test_export_writes_only_accepted_questions_to_their_split(setup):
    client, _, out = setup
    for qid in ("q-1", "q-2"):
        q = next(x for x in client.get("/api/questions").json() if x["id"] == qid)
        client.put(f"/api/questions/{qid}", json={**q, "status": "accepted"})
    counts = client.post("/api/export").json()
    exported = load(out / "dev.jsonl") + load(out / "test.jsonl")
    assert sorted(q.id for q in exported) == ["q-1", "q-2"] and counts["dev"] + counts["test"] == 2
    assert all(q.split == "dev" for q in load(out / "dev.jsonl"))


def test_export_refuses_when_a_result_is_no_longer_what_the_sql_returns(setup, database):
    client, _, out = setup
    q = client.get("/api/questions").json()[1]
    client.put("/api/questions/q-2", json={**q, "status": "accepted"})
    with db.connect(database) as conn:
        conn.execute("UPDATE campaign_metrics SET gmv = 2000")
    try:
        response = client.post("/api/export")
    finally:
        with db.connect(database) as conn:
            conn.execute("UPDATE campaign_metrics SET gmv = 1000.50")
    assert response.status_code == 409 and "q-2" in response.json()["error"]
    assert not (out / "dev.jsonl").exists() and not (out / "test.jsonl").exists()


def test_export_refuses_a_section_the_database_does_not_have(setup):
    client, _, out = setup
    q = client.get("/api/questions").json()[0]
    missing = {"document": json.loads(SOP.model_dump_json()), "heading_path": ["Refund and Return Requests", "Appeals"]}
    client.put("/api/questions/q-1", json={**q, "relevant": [missing], "status": "accepted"})
    response = client.post("/api/export")
    assert response.status_code == 409 and "Appeals" in response.json()["error"]
