"""A local page to review the candidate questions: accept, reject, correct, or write new ones.

    cd llm-service
    uv run python -m evaluation.review        # then open http://127.0.0.1:8765

Every change is written to eval/candidates.jsonl at once, and the question is checked first, the same way it is
checked when it is loaded: a page can't save a question the harness couldn't read. A gold SQL that was edited runs
again, read-only, and its new result is stored. Export writes the accepted questions to eval/dev.jsonl and
eval/test.jsonl, after running every gold SQL once more and finding every labelled section in the database, so the
files hold what the database says today.

It listens on 127.0.0.1 only: it runs SQL against the development database for whoever reaches it.
"""
import argparse
import os
import secrets
import tempfile
from collections.abc import Callable, Iterable
from pathlib import Path

import psycopg
import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import ValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app import db
from evaluation.dataset import Question, load, resolve, save, split_for
from scripts.generate_questions import load_corpus, run_sql

REPO_ROOT = Path(__file__).resolve().parents[2]
EVAL_DIR = REPO_ROOT / "eval"
PAGE = Path(__file__).with_name("review.html")
PORT = 8765


def save_atomically(path: Path, questions: Iterable[Question]) -> None:
    """A crash halfway through writing can't leave half a file: the new one replaces the old one in one step."""
    handle, temporary = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    os.close(handle)
    try:
        save(Path(temporary), questions)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.remove(temporary)


def create_app(candidates: Path, out_dir: Path, connect: Callable = db.connect) -> FastAPI:
    app = FastAPI(title="Question review")
    questions: dict[str, Question] = {q.id: q for q in load(candidates)}

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, e: StarletteHTTPException):
        return JSONResponse({"error": str(e.detail)}, status_code=e.status_code)

    def validated(data: dict) -> Question:
        try:
            return Question.model_validate(data)
        except ValidationError as e:
            raise HTTPException(400, "; ".join(error["msg"].removeprefix("Value error, ") for error in e.errors())) from e

    def run(sql: str):
        with connect() as conn:
            try:
                return run_sql(conn, sql)
            except (ValueError, psycopg.Error) as e:
                raise HTTPException(400, f"the SQL failed: {str(e).splitlines()[0]}") from e

    def with_result(data: dict, old: Question | None) -> dict:
        sql = (data.get("gold_sql") or "").strip() or None
        data["gold_sql"] = sql
        if sql is None:
            data["gold_result"] = None
        elif old is None or sql != old.gold_sql or old.gold_result is None:
            data["gold_result"] = run(sql)   # a changed query gets the result the database gives it now
        else:
            data["gold_result"] = old.gold_result   # never a result typed in the page: only one the SQL returned
        return data

    @app.get("/", response_class=HTMLResponse)
    def page():
        return PAGE.read_text(encoding="utf-8")

    @app.get("/api/questions")
    def all_questions():
        return [q.model_dump(mode="json") for q in questions.values()]

    @app.get("/api/corpus")
    def corpus():
        with connect() as conn:
            chunks = load_corpus(conn)
        documents: dict[str, dict] = {}
        for chunk in chunks:
            ref = chunk.section.document.model_dump(mode="json")
            entry = documents.setdefault(chunk.label, {"label": chunk.label, "ref": ref, "sections": []})
            entry["sections"].append({"heading_path": list(chunk.section.heading_path), "text": chunk.text})
        return list(documents.values())

    @app.put("/api/questions/{question_id}")
    def update(question_id: str, body: dict):
        old = questions.get(question_id)
        if old is None:
            raise HTTPException(404, f"unknown question: {question_id}")
        # Where a question came from is not the reviewer's to change; the split follows from it and from whether the
        # question is to be answered, which the reviewer can change
        fixed = {key: getattr(old, key) for key in ("id", "source", "group")}
        data = with_result({**old.model_dump(mode="json"), **body, **fixed}, old)
        data["split"] = split_for(old.id, old.group, data.get("expected", old.expected), old.source)
        questions[question_id] = question = validated(data)
        save_atomically(candidates, questions.values())
        return question.model_dump(mode="json")

    @app.post("/api/questions", status_code=201)
    def create(body: dict):
        question_id = "h-" + secrets.token_hex(5)
        group = f"human:{question_id}"
        data = with_result({**body, "id": question_id, "source": "human", "group": group,
                            "split": split_for(question_id, group, body.get("expected", "answer"), "human"),
                            "status": body.get("status", "accepted")}, None)
        questions[question_id] = question = validated(data)
        save_atomically(candidates, questions.values())
        return question.model_dump(mode="json")

    @app.post("/api/sql")
    def try_sql(body: dict):
        return {"rows": run(body.get("sql", ""))}

    @app.post("/api/export")
    def export():
        accepted = [q for q in questions.values() if q.status == "accepted"]
        problems = []
        with connect() as conn:
            for q in accepted:
                if q.gold_sql:
                    try:
                        if run_sql(conn, q.gold_sql) != q.gold_result:
                            problems.append(f"{q.id}: its SQL now returns something else; run it again and save")
                    except (ValueError, psycopg.Error) as e:
                        problems.append(f"{q.id}: its SQL fails: {str(e).splitlines()[0]}")
                for section in (*q.relevant, *q.hidden, *q.related):
                    if not resolve(conn, section):
                        problems.append(f"{q.id}: no chunk has {' > '.join(section.heading_path) or section.document}")
        if problems:
            raise HTTPException(409, f"{len(problems)} problems, nothing exported: " + "; ".join(problems[:10]))
        counts = {}
        for split in ("dev", "test"):
            chosen = [q for q in accepted if q.split == split]
            save_atomically(out_dir / f"{split}.jsonl", chosen)
            counts[split] = len(chosen)
        return counts

    return app


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--candidates", type=Path, default=EVAL_DIR / "candidates.jsonl")
    parser.add_argument("--out", type=Path, default=EVAL_DIR, help="where dev.jsonl and test.jsonl go")
    parser.add_argument("--port", type=int, default=PORT)
    args = parser.parse_args()
    print(f"Reviewing {args.candidates} at http://127.0.0.1:{args.port}", flush=True)
    uvicorn.run(create_app(args.candidates, args.out), host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
