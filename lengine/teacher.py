"""AI Teacher: a guided loop per concept instead of a wall of text.

    explain -> check (learner answers a question) -> apply (new situation) -> feedback -> next

Each explanation is grounded in retrieved textbook passages and cites pages; claims outside the
book must be labelled as such. The step and transcript are stored, so a learner can leave and
resume exactly where they were. Depth adapts to the learner's mastery and recent error types.
"""
from __future__ import annotations

import json
import sqlite3

from . import ingest, modes, practice
from .db import j
from .llm import LLM, complete_json

TEACH_SYSTEM = """You are a patient, rigorous teacher. Teach ONE concept from the learner's textbook.
Rules:
- Ground every claim in the excerpts; cite pages like (p.23). If you add something not in the book, mark it "（教材外补充）".
- Explain the underlying logic, not just the definition. Use one concrete example from the book and one everyday analogy (label it 类比).
- Adapt depth: learner mastery is {mastery}; recent errors: {errors}. Low mastery -> start from basics; high -> go faster, focus on edge cases.
- Name one common misconception and why it is wrong.
- Keep it under 350 words. End by asking ONE question that checks understanding (not recall) and wait.
Return JSON {"explanation": "markdown", "check_question": "...", "expected_points": ["..."], "pages": [N]}
Write in {lang}."""

FOLLOWUP_SYSTEM = """You are the same teacher. The learner answered your check question (or asked a follow-up).
Using only the excerpts, respond: acknowledge what is right, correct what is wrong with a hint first, cite pages.
Then, if understanding looks solid, pose ONE application problem in a new situation (different numbers or context).
If it is not solid, re-explain the missing piece differently (different example) and ask a simpler check.
Return JSON {"reply": "markdown", "understood": true|false, "next_question": "...", "pages": [N]}
Write in {lang}."""


def _context(con, concept: dict) -> str:
    hits = ingest.search(con, concept["book_id"], f"{concept['name']} {concept['summary']}", k=6,
                         chapter_id=concept["chapter_id"])
    return "\n".join(f"[p.{h['page']}] {h['text']}" for h in hits)


def _errors(con, concept_id: int) -> list[str]:
    return [r["error_type"] for r in con.execute(
        "SELECT error_type FROM attempts WHERE concept_id=? AND score<0.7 ORDER BY id DESC LIMIT 5", (concept_id,))]


def start_lesson(con: sqlite3.Connection, llm: LLM, concept_id: int, lang: str = "Chinese") -> dict:
    c = dict(con.execute("SELECT c.*, b.domain FROM concepts c JOIN books b ON b.id=c.book_id WHERE c.id=?",
                         (concept_id,)).fetchone())
    prog = practice.concept_progress(con, concept_id)
    tools = modes.choose_tools(c["kind"], c["form"], c["domain"], prog["mastery"])
    system = (TEACH_SYSTEM.replace("{mastery}", str(prog["mastery"]))
              .replace("{errors}", ", ".join(_errors(con, concept_id)) or "none").replace("{lang}", lang))
    data = complete_json(llm, system, f"Concept: {c['name']}\nSummary: {c['summary']}\n"
                         f"Misconceptions: {c['confusions']}\nExcerpts:\n{_context(con, c)}", purpose="teach")
    transcript = [{"role": "teacher", **data}]
    sid = con.execute("INSERT INTO sessions(book_id, chapter_id, concept_id, step, transcript, updated_at) "
                      "VALUES (?,?,?,?,?,datetime('now'))",
                      (c["book_id"], c["chapter_id"], concept_id, "check", j(transcript))).lastrowid
    con.commit()
    return {"session_id": sid, "concept": c["name"], "tools": tools, **data}


def respond(con: sqlite3.Connection, llm: LLM, session_id: int, message: str, lang: str = "Chinese") -> dict:
    s = dict(con.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone())
    c = dict(con.execute("SELECT * FROM concepts WHERE id=?", (s["concept_id"],)).fetchone())
    transcript = json.loads(s["transcript"])
    history = "\n".join(f"{t['role']}: {t.get('explanation') or t.get('reply') or t.get('text')}"
                        f" {t.get('check_question') or t.get('next_question') or ''}" for t in transcript[-6:])
    data = complete_json(llm, FOLLOWUP_SYSTEM.replace("{lang}", lang),
                         f"Concept: {c['name']}\nExcerpts:\n{_context(con, c)}\n\nConversation so far:\n{history}\n\n"
                         f"Learner: {message}", purpose="teach")
    transcript += [{"role": "learner", "text": message}, {"role": "teacher", **data}]
    step = "apply" if data.get("understood") else "check"
    if s["step"] == "apply" and data.get("understood"):
        step = "practice"
    con.execute("UPDATE sessions SET step=?, transcript=?, updated_at=datetime('now') WHERE id=?",
                (step, j(transcript), session_id))
    con.commit()
    return {"step": step, **data}
