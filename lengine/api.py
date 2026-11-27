"""HTTP API + single-page dashboard. Run: uvicorn lengine.api:app --port 8000"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path

from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import dashboard, db, ingest, knowledge, maintenance, practice, teacher, visuals
from .llm import cost_usd, make_llm

DATA = Path(os.environ.get("ENGINE_DATA", "data"))
con = db.ThreadLocalConnection(DATA / "engine.db")
app = FastAPI(title="AI Learning Engine")
STATIC = Path(__file__).parent / "static"
DIST = STATIC / "dist"
if (DIST / "assets").exists():
    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")
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


class BookEdit(BaseModel):
    title: str | None = None
    author: str | None = None
    category: str | None = None


def _index() -> str:
    page = DIST / "index.html" if (DIST / "index.html").exists() else STATIC / "index.html"
    return page.read_text(encoding="utf-8")


@app.get("/", response_class=HTMLResponse)
def index():
    return _index()


@app.get("/api/home")
def home():
    return dashboard.home(con)


@app.get("/api/overview")
def overview():
    return dashboard.overview(con)


@app.get("/api/library")
def library():
    return dashboard.library(con)


@app.post("/api/books")
async def upload(file: UploadFile, title: str | None = None, domain: str = "general", license: str | None = None,
                 author: str | None = None, category: str | None = None):
    dest = DATA / "books" / Path(file.filename).name
    dest.parent.mkdir(parents=True, exist_ok=True)
    h = hashlib.sha256()
    with dest.open("wb") as f:
        while chunk := file.file.read(1 << 20):
            h.update(chunk)
            f.write(chunk)
    sha = h.hexdigest()
    dup = con.execute("SELECT id FROM books WHERE file_sha256=?", (sha,)).fetchone()
    if dup:  # same file already imported: don't process (or pay for) it twice
        return {"book_id": dup["id"], "duplicate": True}
    book_id = ingest.ingest_pdf(con, str(dest), title, domain, license)
    con.execute("UPDATE books SET file_sha256=?, author=?, category=? WHERE id=?", (sha, author, category, book_id))
    con.commit()
    return {"book_id": book_id, "duplicate": False}


def _book_or_404(book_id: int) -> None:
    if not con.execute("SELECT 1 FROM books WHERE id=?", (book_id,)).fetchone():
        raise HTTPException(404, "book not found")


@app.get("/api/books/{book_id}")
def book(book_id: int):
    _book_or_404(book_id)
    o = dashboard.book_detail(con, book_id)
    o["path"] = [{"id": c["id"], "name": c["name"], "chapter_id": c["chapter_id"]}
                 for c in knowledge.learning_path(con, book_id)]
    # older clients
    o["weak"] = [c for c in o["concepts"] if c["status"] in dashboard.STATUS_WEAK]
    from . import review
    o["due"] = review.due_cards(con, book_id)
    return o


@app.patch("/api/books/{book_id}")
def edit_book(book_id: int, body: BookEdit):
    _book_or_404(book_id)
    for field in ("title", "author", "category"):
        value = getattr(body, field)
        if value is not None:
            con.execute(f"UPDATE books SET {field}=?, updated_at=datetime('now') WHERE id=?", (value.strip() or None, book_id))
    con.commit()
    return dashboard.library(con)


@app.get("/api/books/{book_id}/cover")
def cover(book_id: int):
    """The book's real first page, rendered once and cached. 404 when the PDF is not on this machine."""
    r = con.execute("SELECT source_path, cover_path FROM books WHERE id=?", (book_id,)).fetchone()
    if not r:
        raise HTTPException(404)
    cached = DATA / "covers" / f"{book_id}.jpg"
    if cached.exists():
        return FileResponse(cached, headers={"Cache-Control": "max-age=86400"})
    src = Path(r["source_path"] or "")
    if not src.exists():
        src = DATA / "books" / src.name
    if not src.name or not src.exists():
        raise HTTPException(404, "PDF not available on this computer")
    import pypdfium2 as pdfium
    cached.parent.mkdir(parents=True, exist_ok=True)
    pdf = pdfium.PdfDocument(str(src))
    page = pdf[0]
    page.render(scale=360 / page.get_width()).to_pil().convert("RGB").save(cached, quality=82)
    pdf.close()
    con.execute("UPDATE books SET cover_path=? WHERE id=?", (str(cached), book_id))
    con.commit()
    return FileResponse(cached, headers={"Cache-Control": "max-age=86400"})


@app.get("/api/maintenance/repair")
def repair_status():
    return maintenance.status(con)


@app.post("/api/maintenance/repair")
def repair_start():
    return maintenance.start(con, llm())


@app.post("/api/chapters/{chapter_id}/map")
def build_map(chapter_id: int):
    return {"concepts": knowledge.build_chapter_map(con, llm(), chapter_id)}


@app.get("/api/concepts/{concept_id}")
def concept(concept_id: int):
    r = con.execute("SELECT c.*, b.title AS book_title, ch.title AS chapter_title FROM concepts c "
                    "JOIN books b ON b.id=c.book_id LEFT JOIN chapters ch ON ch.id=c.chapter_id WHERE c.id=?",
                    (concept_id,)).fetchone()
    if not r:
        raise HTTPException(404, "concept not found")
    c = dict(r)
    c["requires"] = db.rows(con.execute("SELECT c.id, c.name FROM prerequisites p JOIN concepts c ON c.id=p.requires_id "
                                        "WHERE p.concept_id=?", (concept_id,)))
    c["required_by"] = db.rows(con.execute("SELECT c.id, c.name FROM prerequisites p JOIN concepts c ON c.id=p.concept_id "
                                           "WHERE p.requires_id=?", (concept_id,)))
    c["attempts"] = db.rows(con.execute("SELECT a.created_at, a.score, a.error_type, a.feedback, q.level, q.prompt "
                                        "FROM attempts a JOIN questions q ON q.id=a.question_id WHERE a.concept_id=? "
                                        "ORDER BY a.id DESC LIMIT 20", (concept_id,)))
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
    p = Path(r["path"])
    if not p.exists():  # database copied from another machine: look in this machine's artifacts folder
        p = DATA / "artifacts" / p.name
    if not p.exists():
        raise HTTPException(404)
    return FileResponse(p)


@app.get("/api/review/due")
def due():
    from . import review
    return review.due_cards(con)


@app.get("/{path:path}", response_class=HTMLResponse, include_in_schema=False)
def spa(path: str):
    """Client-side routes (e.g. /library/3) all load the single-page app."""
    if path.startswith("api/"):
        raise HTTPException(404)
    return _index()
