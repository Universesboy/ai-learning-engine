"""Dashboard data: per-book progress, weak spots, due reviews, recent scores, trend and model usage."""
from __future__ import annotations

import sqlite3

from . import practice, review
from .db import rows


def book_overview(con: sqlite3.Connection, book_id: int) -> dict:
    book = dict(con.execute("SELECT * FROM books WHERE id=?", (book_id,)).fetchone())
    chapters = rows(con.execute("SELECT * FROM chapters WHERE book_id=? ORDER BY ord", (book_id,)))
    concepts = rows(con.execute("SELECT id, chapter_id, name, kind, form, pages FROM concepts WHERE book_id=?", (book_id,)))
    for c in concepts:
        c.update(practice.concept_progress(con, c["id"]))
        c["forgetting_risk"] = review.forgetting_risk(con, c["id"], c["kind"])
    for ch in chapters:
        cs = [c for c in concepts if c["chapter_id"] == ch["id"]]
        ch["concepts"] = len(cs)
        ch["mastered"] = sum(c["status"] == "mastered" for c in cs)
        ch["started"] = sum(c["status"] != "not_started" for c in cs)
        ch["state"] = ("未建图" if not cs else "已完成" if ch["mastered"] == len(cs)
                       else "学习中" if ch["started"] else "待学习")
    weak = [c for c in concepts if c["status"] in ("needs_relearning", "shaky", "cannot_apply_yet")]
    return {"book": book, "chapters": chapters, "concepts": concepts, "weak": weak,
            "due": review.due_cards(con, book_id), "mistakes": practice.mistakes(con, book_id)[:20]}


def home(con: sqlite3.Connection) -> dict:
    books = []
    for b in rows(con.execute("SELECT * FROM books ORDER BY id DESC")):
        o = book_overview(con, b["id"])
        total = len(o["concepts"])
        books.append({**b, "concepts": total, "mastered": sum(c["status"] == "mastered" for c in o["concepts"]),
                      "weak": len(o["weak"]), "due": len(o["due"]),
                      "chapters_done": sum(ch["state"] == "已完成" for ch in o["chapters"]), "chapters": len(o["chapters"])})
    trend = rows(con.execute("SELECT date(created_at) AS day, round(avg(score),3) AS avg_score, count(*) AS n "
                             "FROM attempts GROUP BY day ORDER BY day DESC LIMIT 30"))
    recent = rows(con.execute("SELECT a.created_at, a.score, a.error_type, q.level, c.name AS concept FROM attempts a "
                              "JOIN questions q ON q.id=a.question_id JOIN concepts c ON c.id=a.concept_id "
                              "ORDER BY a.id DESC LIMIT 10"))
    usage = rows(con.execute("SELECT purpose, model, count(*) calls, sum(prompt_tokens) prompt_tokens, "
                             "sum(completion_tokens) completion_tokens, round(sum(seconds),1) seconds "
                             "FROM llm_usage GROUP BY purpose, model"))
    from .llm import cost_usd
    for u in usage:
        c = cost_usd(u["model"], u["prompt_tokens"] or 0, u["completion_tokens"] or 0)
        u["usd"] = round(c, 4) if c is not None else None
    return {"books": books, "trend": list(reversed(trend)), "recent": recent, "usage": usage,
            "due_total": len(review.due_cards(con))}
