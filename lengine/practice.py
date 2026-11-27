"""Practice & Assessment.

Questions are generated at three levels (recall / understand / apply) so mastery is not judged by
memory questions alone. Types: single choice, true/false, fill-in, short answer, application
problem, case analysis. Choice/true-false/fill-in are graded deterministically; open answers are
graded by the model against a rubric, with an error type so the review engine knows *why*.
"""
from __future__ import annotations

import json
import sqlite3

from . import ingest, mastery, review
from .db import j, rows
from .llm import LLM, complete_json
from .similarity import similarity

ERROR_TYPES = ["none", "misconception", "incomplete", "calculation", "misread_question",
               "cannot_transfer", "forgot_fact"]

GEN_SYSTEM = """You write practice questions for ONE concept from a textbook, grounded ONLY in the excerpts given.
Return JSON {"questions":[{"qtype":"single_choice|true_false|fill_in|short_answer|application|case_analysis",
"level":"recall|understand|apply","prompt":"...","choices":["A ...","B ..."] (single_choice only),
"answer":"correct choice letter, true/false, the exact fill-in word, or a model answer",
"rubric":"for open answers: the 2-4 points a full answer must contain","pages":[N]}]}
Write {n} questions: at least one recall, one understand and two apply (apply = a NEW situation or numbers
not in the book, e.g. predict what happens to price and quantity). Use the concept's known misconceptions
as distractors. Write in {lang}."""

GRADE_SYSTEM = """Grade a learner's answer against the rubric and model answer. Be fair but strict about reasoning.
Return JSON {"score": 0..1, "error_type": one of %s, "feedback": "2-4 sentences: what is right, what is missing,
and one question that makes the learner think rather than giving the full answer away", "pages": [N]}
Write the feedback in the same language as the question.""" % ERROR_TYPES


def generate_questions(con: sqlite3.Connection, llm: LLM, concept_id: int, n: int = 5, lang: str = "Chinese") -> list[dict]:
    c = dict(con.execute("SELECT * FROM concepts WHERE id=?", (concept_id,)).fetchone())
    hits = ingest.search(con, c["book_id"], f"{c['name']} {c['summary']}", k=6, chapter_id=c["chapter_id"])
    excerpts = "\n".join(f"[p.{h['page']}] {h['text']}" for h in hits)
    user = (f"Concept: {c['name']}\nSummary: {c['summary']}\nMisconceptions: {c['confusions']}\n"
            f"Formulas: {c['formulas']}\n\nExcerpts:\n{excerpts}")
    data = complete_json(llm, GEN_SYSTEM.replace("{n}", str(n)).replace("{lang}", lang), user, purpose="practice", book_id=c["book_id"])
    out = []
    for q in data.get("questions", []):
        if not q.get("prompt") or q.get("level") not in ("recall", "understand", "apply"):
            continue
        qid = con.execute(
            "INSERT INTO questions(concept_id, qtype, level, prompt, choices, answer, rubric, pages) VALUES (?,?,?,?,?,?,?,?)",
            (concept_id, q.get("qtype", "short_answer"), q["level"], q["prompt"], j(q.get("choices")),
             str(q.get("answer", "")), q.get("rubric", ""), j(q.get("pages", [])))).lastrowid
        out.append({**q, "id": qid})
    con.commit()
    return out


def _deterministic(q: dict, response: str) -> dict | None:
    ans = (q["answer"] or "").strip()
    r = response.strip()
    if q["qtype"] == "single_choice":
        ok = r[:1].upper() == ans[:1].upper()
        return {"score": 1.0 if ok else 0.0, "error_type": "none" if ok else "misconception",
                "feedback": "正确。" if ok else f"正确答案是 {ans}。"}
    if q["qtype"] == "true_false":
        norm = lambda s: s.lower() in ("true", "t", "对", "正确", "是", "yes", "√")
        ok = norm(r) == norm(ans)
        return {"score": 1.0 if ok else 0.0, "error_type": "none" if ok else "misconception",
                "feedback": "正确。" if ok else f"答案是 {ans}。"}
    if q["qtype"] == "fill_in":
        s = similarity(r, ans)
        return {"score": 1.0 if s >= 0.85 else 0.0, "error_type": "none" if s >= 0.85 else "forgot_fact",
                "feedback": "正确。" if s >= 0.85 else f"答案是 {ans}。"}
    return None


def grade(con: sqlite3.Connection, llm: LLM | None, question_id: int, response: str) -> dict:
    q = dict(con.execute("SELECT * FROM questions WHERE id=?", (question_id,)).fetchone())
    result = _deterministic(q, response)
    if result is None:
        if llm is None:
            raise RuntimeError("open answers need a model to grade")
        result = complete_json(llm, GRADE_SYSTEM,
                               f"Question: {q['prompt']}\nModel answer: {q['answer']}\nRubric: {q['rubric']}\n"
                               f"Learner answer: {response}", purpose="grading",
                               book_id=_book_of(con, q["concept_id"]))
        result["score"] = max(0.0, min(1.0, float(result.get("score", 0))))
        if result.get("error_type") not in ERROR_TYPES:
            result["error_type"] = "incomplete" if result["score"] < 1 else "none"
    con.execute("INSERT INTO attempts(question_id, concept_id, response, score, error_type, feedback) VALUES (?,?,?,?,?,?)",
                (question_id, q["concept_id"], response, result["score"], result["error_type"], result.get("feedback", "")))
    kind = con.execute("SELECT kind FROM concepts WHERE id=?", (q["concept_id"],)).fetchone()["kind"]
    sched = review.review(con, q["concept_id"], kind, result["score"])
    con.commit()
    return {**result, "next_review": sched["due"], "status": concept_progress(con, q["concept_id"])}


def _book_of(con, concept_id: int) -> int | None:
    r = con.execute("SELECT book_id FROM concepts WHERE id=?", (concept_id,)).fetchone()
    return r["book_id"] if r else None


def concept_progress(con: sqlite3.Connection, concept_id: int) -> dict:
    by_level: dict[str, list[float]] = {}
    for r in con.execute("SELECT a.score, q.level FROM attempts a JOIN questions q ON q.id=a.question_id "
                         "WHERE a.concept_id=? ORDER BY a.id", (concept_id,)):
        by_level.setdefault(r["level"], []).append(r["score"])
    return mastery.concept_status(by_level)


def mistakes(con: sqlite3.Connection, book_id: int) -> list[dict]:
    return rows(con.execute(
        "SELECT a.id, a.created_at, a.response, a.score, a.error_type, a.feedback, q.prompt, q.answer, q.level, "
        "c.name AS concept, q.pages FROM attempts a JOIN questions q ON q.id=a.question_id "
        "JOIN concepts c ON c.id=a.concept_id WHERE c.book_id=? AND a.score < 0.7 ORDER BY a.id DESC", (book_id,)))


def public(q: dict) -> dict:
    """A question as shown to the learner (no answer or rubric)."""
    return {k: (json.loads(q[k]) if k in ("choices", "pages") and isinstance(q.get(k), str) else q.get(k))
            for k in ("id", "qtype", "level", "prompt", "choices", "pages")}
