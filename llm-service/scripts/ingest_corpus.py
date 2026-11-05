"""Uploads every document in sop_docs/ and campaign_docs/ through the backend's API, as the people who would, and
waits until llm-service has ingested them.

Each document is uploaded by a staff member of its department, so it takes effect at once. A company-wide (ALL)
document needs the uploader's superior: the staff member's own manager approves it through the API too. So the
corpus goes through the same rules, outbox, Kafka and consumer as any upload, instead of being written into the
database behind their back. Only who is who and which campaign is which are read from the database, because the
API has no endpoint that lists users, and campaigns can only be found by month there.

    cd llm-service
    uv run python -m scripts.ingest_corpus [--backend http://localhost:8081]

Needs the backend, Kafka and the consumer running; the consumer embeds with the real API (300 chunks cost well
under one cent). A document that already exists gets 409 and is skipped, so the script can run again after a
failure. On a fresh database, run scripts.generate_mock_data first.
"""
import argparse
import sys
import time
from pathlib import Path

import httpx
import yaml

from app import db
from scripts.corpus_spec import ALL
from scripts.generate_mock_data import DEPARTMENTS

REPO_ROOT = Path(__file__).resolve().parents[2]
FOLDERS = (REPO_ROOT / "sop_docs", REPO_ROOT / "campaign_docs")
WAIT_SECONDS = 900          # for the consumer to ingest everything; it handles one upload at a time
FINISHED = {"done", "failed", "rejected"}


def frontmatter(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        raise ValueError(f"{path.name} has no frontmatter")
    return yaml.safe_load(text.split("---\n", 2)[1])


def people(conn) -> dict[str, dict[str, int]]:
    """For each department, the first staff member and the manager, by user id."""
    found: dict[str, dict[str, int]] = {}
    for user_id, role, department in conn.execute(
            """SELECT u.user_id, u.role, d.name FROM users u JOIN departments d USING (department_id)
               WHERE u.role IN ('staff', 'manager') ORDER BY u.user_id"""):
        found.setdefault(department, {}).setdefault(role, user_id)
    return found


def campaign_id(conn, fields: dict) -> int:
    """By department, name and start date. The hand-written samples have no start date: their names are unique."""
    query = """SELECT c.campaign_id FROM campaigns c JOIN departments d USING (department_id)
               WHERE d.name = %(department)s AND c.name = %(name)s
                 AND (%(start)s::date IS NULL OR c.start_date = %(start)s::date)"""
    rows = conn.execute(query, {"department": fields["department"], "name": fields["campaign_name"],
                                "start": fields.get("campaign_start")}).fetchall()
    if len(rows) != 1:
        raise LookupError(f"{len(rows)} campaigns match {fields['department']} / {fields['campaign_name']}")
    return rows[0][0]


def upload_form(conn, fields: dict) -> dict[str, str]:
    doc_type = fields.get("doc_type", "sop")
    form = {"contentKind": "document", "docType": doc_type, "mode": "new"}
    if doc_type == "sop":
        form |= {"title": fields["title"], "scope": "all" if fields["department"] == ALL else "department"}
        if fields.get("category"):
            form["category"] = fields["category"]
    else:
        form["campaignId"] = str(campaign_id(conn, fields))
    return form


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--backend", default="http://localhost:8081")
    args = parser.parse_args()

    paths = sorted(p for folder in FOLDERS for p in folder.glob("*.md") if p.name != "README.md")
    company_wide_uploaders = list(DEPARTMENTS)   # company-wide documents are spread over the departments
    pending: dict[str, tuple[Path, int]] = {}    # upload id -> (file, uploader)
    skipped, refused = [], []
    with db.connect() as conn, httpx.Client(base_url=args.backend, timeout=30) as api:
        who = people(conn)
        for index, path in enumerate(paths):
            fields = frontmatter(path)
            department = fields["department"]
            uploader_department = company_wide_uploaders[index % len(company_wide_uploaders)] if department == ALL else department
            uploader = who[uploader_department]["staff"]
            response = api.post("/documents/upload", headers={"X-User-Id": str(uploader)},
                                data=upload_form(conn, fields),
                                files={"file": (path.name, path.read_bytes(), "text/markdown")})
            if response.status_code == 409:
                skipped.append(path.name)
                continue
            if response.status_code != 202:
                refused.append((path.name, response.status_code, response.text))
                continue
            upload_id = response.json()["uploadId"]
            if department == ALL:
                manager = who[uploader_department]["manager"]
                approval = api.post(f"/documents/approvals/{upload_id}/approve", headers={"X-User-Id": str(manager)})
                if approval.status_code != 200:
                    refused.append((path.name, approval.status_code, approval.text))
                    continue
            pending[upload_id] = (path, uploader)
        print(f"{len(paths)} files: {len(pending)} uploaded, {len(skipped)} already there, {len(refused)} refused", flush=True)

        statuses: dict[str, tuple[str, str | None]] = {}
        deadline = time.monotonic() + WAIT_SECONDS
        while pending.keys() - statuses.keys() and time.monotonic() < deadline:
            for upload_id in pending.keys() - statuses.keys():
                path, uploader = pending[upload_id]
                body = api.get(f"/documents/{upload_id}/status", headers={"X-User-Id": str(uploader)}).json()
                if body["status"] in FINISHED:
                    statuses[upload_id] = (body["status"], body.get("error"))
            time.sleep(1)

        counts = {}
        for status, _ in statuses.values():
            counts[status] = counts.get(status, 0) + 1
        print(f"ingested: {counts}, still waiting: {len(pending) - len(statuses)}", flush=True)
        for upload_id, (status, error) in statuses.items():
            if status != "done":
                print(f"  {pending[upload_id][0].name}: {status}: {error}")
        for name, code, text in refused:
            print(f"  refused {name}: {code} {text[:200]}")

        print("chunks each department's staff can search (its own and ALL):")
        for department in DEPARTMENTS:
            visible = conn.execute("SELECT count(*) FROM doc_chunks WHERE department IN (%s, 'ALL')", (department,)).fetchone()[0]
            print(f"  {department}: {visible}")
    sys.exit(1 if refused or len(statuses) < len(pending) or any(s != "done" for s, _ in statuses.values()) else 0)


if __name__ == "__main__":
    main()
