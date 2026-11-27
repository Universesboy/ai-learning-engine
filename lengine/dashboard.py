"""Dashboard data, computed from the database only (no placeholders).

Everything is aggregated with a fixed number of queries per page, so the cost does not grow with one
query per concept as the library grows.
"""
from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from datetime import datetime, time, timedelta, timezone

from fsrs import Card

from . import mastery, review
from .db import rows
from .llm import cost_usd

STATUS_WEAK = ("needs_relearning", "shaky", "cannot_apply_yet")


def _progress(con: sqlite3.Connection, book_id: int | None = None, until: str | None = None) -> dict[int, dict]:
    """Mastery status per concept from all attempts (one query)."""
    sql = ("SELECT a.concept_id, a.score, q.level FROM attempts a JOIN questions q ON q.id=a.question_id "
           "JOIN concepts c ON c.id=a.concept_id WHERE 1=1")
    args: list = []
    if book_id:
        sql += " AND c.book_id=?"
        args.append(book_id)
    if until:
        sql += " AND a.created_at < ?"
        args.append(until)
    by: dict[int, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for r in con.execute(sql + " ORDER BY a.id", args):
        by[r["concept_id"]][r["level"]].append(r["score"])
    return {cid: mastery.concept_status(levels) for cid, levels in by.items()}


def _cards(con: sqlite3.Connection, book_id: int | None, now: datetime) -> dict[int, dict]:
    sql = "SELECT k.concept_id, k.state, k.due, c.kind FROM cards k JOIN concepts c ON c.id=k.concept_id"
    args: list = []
    if book_id:
        sql += " WHERE c.book_id=?"
        args.append(book_id)
    out = {}
    for r in con.execute(sql, args):
        card = Card.from_json(r["state"])
        sched = review._schedulers.get(r["kind"], review._schedulers["concept"])
        out[r["concept_id"]] = {"due": r["due"], "retrievability": round(sched.get_card_retrievability(card, now), 3)}
    return out


def _end_of_today(now: datetime) -> datetime:
    local = now.astimezone()
    return datetime.combine(local.date(), time(23, 59, 59), tzinfo=local.tzinfo).astimezone(timezone.utc)


def _concepts(con, book_id: int | None, now: datetime) -> list[dict]:
    sql = ("SELECT c.id, c.book_id, c.chapter_id, c.name, c.kind, c.form, c.pages, c.summary FROM concepts c"
           + (" WHERE c.book_id=?" if book_id else ""))
    cs = rows(con.execute(sql, (book_id,) if book_id else ()))
    prog, cards = _progress(con, book_id), _cards(con, book_id, now)
    empty = mastery.concept_status({})
    for c in cs:
        c.update(prog.get(c["id"], empty))
        card = cards.get(c["id"])
        c["due"] = card["due"] if card else None
        c["forgetting_risk"] = round(1 - card["retrievability"], 3) if card else None
        c["pages"] = json.loads(c["pages"] or "[]")
    return cs


def _usage(con, book_id: int | None = None) -> dict:
    sql = ("SELECT purpose, model, count(*) calls, sum(prompt_tokens) prompt_tokens, sum(completion_tokens) completion_tokens,"
           " round(sum(seconds),1) seconds FROM llm_usage" + (" WHERE book_id=?" if book_id else "") + " GROUP BY purpose, model")
    items = rows(con.execute(sql, (book_id,) if book_id else ()))
    total = 0.0
    for u in items:
        c = cost_usd(u["model"], u["prompt_tokens"] or 0, u["completion_tokens"] or 0)
        u["usd"] = round(c, 4) if c is not None else None
        total += c or 0
    return {"items": items, "usd": round(total, 4), "calls": sum(u["calls"] for u in items)}


def _book_summary(b: dict, concepts: list[dict], chapters: list[dict], due_cut: str) -> dict:
    total = len(concepts)
    mastered = sum(c["status"] == "mastered" for c in concepts)
    started = sum(c["status"] != "not_started" for c in concepts)
    mapped = sum(1 for ch in chapters if ch.get("map_built_at"))
    return {
        **{k: b.get(k) for k in ("id", "title", "author", "category", "domain", "created_at", "page_count", "status")},
        "chapters": len(chapters), "chapters_mapped": mapped, "concepts": total, "mastered": mastered,
        "started": started, "weak": sum(c["status"] in STATUS_WEAK for c in concepts),
        "due": sum(1 for c in concepts if c["due"] and c["due"] <= due_cut),
        "progress": round(mastered / total, 3) if total else 0.0,
        "ai_status": "done" if chapters and mapped == len(chapters) else "partial" if mapped else "not_started",
    }


def library(con: sqlite3.Connection, now: datetime | None = None) -> list[dict]:
    now = now or datetime.now(timezone.utc)
    due_cut = _end_of_today(now).isoformat()
    books = rows(con.execute("SELECT * FROM books ORDER BY id DESC"))
    concepts = _concepts(con, None, now)
    chapters = rows(con.execute("SELECT id, book_id, map_built_at FROM chapters"))
    last = {r["book_id"]: r["last"] for r in con.execute(
        "SELECT c.book_id, max(a.created_at) last FROM attempts a JOIN concepts c ON c.id=a.concept_id GROUP BY c.book_id")}
    repair = defaultdict(int)
    from .knowledge import chapters_needing_repair
    for ch in chapters_needing_repair(con):
        repair[ch["book_id"]] += 1
    out = []
    for b in books:
        s = _book_summary(b, [c for c in concepts if c["book_id"] == b["id"]],
                          [ch for ch in chapters if ch["book_id"] == b["id"]], due_cut)
        s["last_studied"] = last.get(b["id"])
        s["needs_repair"] = repair.get(b["id"], 0)
        out.append(s)
    return out


def _mastery_trend(con, days: int = 30) -> list[dict]:
    """Concepts mastered as of the end of each day that had attempts (replayed from history)."""
    day_rows = [r["day"] for r in con.execute(
        "SELECT DISTINCT date(created_at) day FROM attempts ORDER BY day DESC LIMIT ?", (days,))]
    out = []
    for day in reversed(day_rows):
        nxt = (datetime.fromisoformat(day) + timedelta(days=1)).strftime("%Y-%m-%d")
        prog = _progress(con, until=nxt)
        n = con.execute("SELECT count(*) n, round(avg(score),3) avg FROM attempts WHERE date(created_at)=?", (day,)).fetchone()
        out.append({"day": day, "mastered": sum(p["status"] == "mastered" for p in prog.values()),
                    "evidence": len(prog), "attempts": n["n"], "avg_score": n["avg"]})
    return out


def overview(con: sqlite3.Connection, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    due_cut = _end_of_today(now).isoformat()
    lib = library(con, now)
    concepts = _concepts(con, None, now)
    titles = {b["id"]: b["title"] for b in lib}
    total = len(concepts)
    mastered = sum(c["status"] == "mastered" for c in concepts)
    weak = sorted((c for c in concepts if c["status"] in STATUS_WEAK), key=lambda c: c["mastery"])[:8]
    due = sorted((c for c in concepts if c["due"] and c["due"] <= due_cut), key=lambda c: c["due"])
    recent = rows(con.execute(
        "SELECT a.id, a.created_at, a.score, a.error_type, q.level, q.qtype, c.id concept_id, c.name concept, c.book_id "
        "FROM attempts a JOIN questions q ON q.id=a.question_id JOIN concepts c ON c.id=a.concept_id "
        "ORDER BY a.id DESC LIMIT 12"))
    for r in recent:
        r["book"] = titles.get(r["book_id"])
    learning = sorted((b for b in lib if b["started"]), key=lambda b: b["last_studied"] or "", reverse=True)
    status_counts = defaultdict(int)
    for c in concepts:
        status_counts[c["status"]] += 1
    trend = _mastery_trend(con)
    week_ago = trend[-8]["mastered"] if len(trend) >= 8 else (trend[0]["mastered"] if trend else 0)
    return {
        "kpis": {
            "due_today": len(due), "books": len(lib), "books_learning": len(learning),
            "concepts": total, "mastered": mastered, "mastery_rate": round(mastered / total, 4) if total else 0.0,
            "with_evidence": sum(c["status"] != "not_started" for c in concepts),
            "attempts": con.execute("SELECT count(*) FROM attempts").fetchone()[0],
            "mastered_change": mastered - week_ago if trend else None,
        },
        "status_counts": dict(status_counts),
        "due": [{**{k: c[k] for k in ("id", "name", "book_id", "due", "forgetting_risk", "status")},
                 "book": titles.get(c["book_id"])} for c in due[:10]],
        "weak": [{**{k: c[k] for k in ("id", "name", "book_id", "status", "mastery", "can_apply")},
                  "book": titles.get(c["book_id"])} for c in weak],
        "recent": recent, "learning": learning[:4], "trend": trend, "usage": _usage(con),
        "needs_repair": sum(b["needs_repair"] for b in lib),
    }


def book_detail(con: sqlite3.Connection, book_id: int, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    due_cut = _end_of_today(now).isoformat()
    book = dict(con.execute("SELECT * FROM books WHERE id=?", (book_id,)).fetchone())
    chapters = rows(con.execute("SELECT * FROM chapters WHERE book_id=? ORDER BY ord", (book_id,)))
    concepts = _concepts(con, book_id, now)
    prereq = rows(con.execute(
        "SELECT p.concept_id source, p.requires_id target FROM prerequisites p JOIN concepts c ON c.id=p.concept_id "
        "WHERE c.book_id=?", (book_id,)))
    from .knowledge import chapters_needing_repair
    repair_ids = {c["id"] for c in chapters_needing_repair(con, book_id)}
    for ch in chapters:
        cs = [c for c in concepts if c["chapter_id"] == ch["id"]]
        ch["concepts"] = len(cs)
        ch["mastered"] = sum(c["status"] == "mastered" for c in cs)
        ch["started"] = sum(c["status"] != "not_started" for c in cs)
        ch["weak"] = sum(c["status"] in STATUS_WEAK for c in cs)
        ch["due"] = sum(1 for c in cs if c["due"] and c["due"] <= due_cut)
        ch["needs_repair"] = ch["id"] in repair_ids
        for k in ("core_ideas", "arguments"):
            ch[k] = json.loads(ch.get(k) or "[]")
    from . import practice
    return {"book": book, "summary": _book_summary(book, concepts, chapters, due_cut), "chapters": chapters,
            "concepts": concepts, "prerequisites": prereq, "mistakes": practice.mistakes(con, book_id)[:30],
            "usage": _usage(con, book_id)}


# kept for the scripted run and older callers
def home(con: sqlite3.Connection) -> dict:
    o = overview(con)
    return {"books": library(con), "trend": o["trend"], "recent": o["recent"], "usage": o["usage"]["items"],
            "due_total": o["kpis"]["due_today"]}


def book_overview(con: sqlite3.Connection, book_id: int) -> dict:
    d = book_detail(con, book_id)
    weak = [c for c in d["concepts"] if c["status"] in STATUS_WEAK]
    return {**d, "weak": weak, "due": review.due_cards(con, book_id)}
