"""HTTP API + single-page dashboard. Run: uvicorn lengine.api:app --port 8000"""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel

from . import dashboard, db, ingest, knowledge, practice, teacher, visuals
from .llm import cost_usd, make_llm

DATA = Path(os.environ.get("ENGINE_DATA", "data"))
con = db.connect(DATA / "engine.db")
app = FastAPI(title="AI Learning Engine")
_llm = None


def llm():
    global _llm
    if _llm is None:
        try:
            _llm = make_llm(con)
        except RuntimeError as e:
            raise HTTPException(503, str(e))
    return _llm


class Reply(BaseModel):
    message: str


class Answer(BaseModel):
    response: str


@app.get("/", response_class=HTMLResponse)
def index():
    return (Path(__file__).parent / "static" / "index.html").read_text(encoding="utf-8")


@app.get("/api/home")
def home():
    return dashboard.home(con)


@app.post("/api/books")
async def upload(file: UploadFile, title: str | None = None, domain: str = "general", license: str | None = None):
    dest = DATA / "books" / file.filename
    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open("wb") as f:
        shutil.copyfileobj(file.file, f)
    return {"book_id": ingest.ingest_pdf(con, str(dest), title, domain, license)}


@app.get("/api/books/{book_id}")
def book(book_id: int):
    o = dashboard.book_overview(con, book_id)
    o["path"] = [{"id": c["id"], "name": c["name"], "chapter_id": c["chapter_id"]}
                 for c in knowledge.learning_path(con, book_id)]
    return o


@app.post("/api/chapters/{chapter_id}/map")
def build_map(chapter_id: int):
    return {"concepts": knowledge.build_chapter_map(con, llm(), chapter_id)}


@app.get("/api/concepts/{concept_id}")
def concept(concept_id: int):
    c = dict(con.execute("SELECT * FROM concepts WHERE id=?", (concept_id,)).fetchone())
    c["progress"] = practice.concept_progress(con, concept_id)
    c["artifacts"] = db.rows(con.execute("SELECT id, kind, meta FROM artifacts WHERE concept_id=?", (concept_id,)))
    return c


@app.post("/api/concepts/{concept_id}/lesson")
def lesson(concept_id: int):
    return teacher.start_lesson(con, llm(), concept_id)


@app.post("/api/sessions/{session_id}/reply")
def reply(session_id: int, body: Reply):
    return teacher.respond(con, llm(), session_id, body.message)


@app.post("/api/concepts/{concept_id}/questions")
def questions(concept_id: int, n: int = 5):
    existing = db.rows(con.execute("SELECT * FROM questions WHERE concept_id=? AND id NOT IN "
                                   "(SELECT question_id FROM attempts)", (concept_id,)))
    qs = existing or practice.generate_questions(con, llm(), concept_id, n)
    return [practice.public(q) for q in qs]


@app.post("/api/questions/{question_id}/answer")
def answer(question_id: int, body: Answer):
    q = con.execute("SELECT qtype FROM questions WHERE id=?", (question_id,)).fetchone()
    needs_llm = q["qtype"] not in ("single_choice", "true_false", "fill_in")
    return practice.grade(con, llm() if needs_llm else None, question_id, body.response)


@app.post("/api/concepts/{concept_id}/visual")
def visual(concept_id: int, kind: str = "interactive_sim"):
    if kind == "manim":
        return visuals.manim_animation(con, llm(), concept_id, str(DATA / "artifacts"))
    return visuals.interactive_page(con, llm(), concept_id, str(DATA / "artifacts"))


@app.get("/api/artifacts/{artifact_id}")
def artifact(artifact_id: int):
    r = con.execute("SELECT path FROM artifacts WHERE id=?", (artifact_id,)).fetchone()
    if not r or not r["path"]:
        raise HTTPException(404)
    return FileResponse(r["path"])


@app.get("/api/review/due")
def due():
    from . import review
    return review.due_cards(con)
