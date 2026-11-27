"""Repair chapters damaged by the pre-2.0 concept key collision, in the background.

Interim runner: a thread pool and an in-memory status. Phase 3 moves long work onto the persistent
job table; this repair is a one-off, so it does not need to survive a restart (re-running it simply
picks up the chapters that are still damaged).
"""
from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

from . import knowledge

# measured 2026-11-27: Sonnet map of a ~9-page chapter of this book costs $0.037-0.046
USD_PER_CHAPTER = 0.045

_lock = threading.Lock()
_state: dict = {"running": False, "total": 0, "done": 0, "failed": [], "chapters": []}


def status(con) -> dict:
    pending = knowledge.chapters_needing_repair(con)
    with _lock:
        return {**_state, "pending": len(pending), "estimate_usd": round(len(pending) * USD_PER_CHAPTER, 2),
                "pending_chapters": [{"id": c["id"], "book_id": c["book_id"], "title": c["title"]} for c in pending]}


def start(con, llm, workers: int = 4) -> dict:
    chapters = knowledge.chapters_needing_repair(con)
    with _lock:
        if _state["running"]:
            return dict(_state)
        _state.update(running=True, total=len(chapters), done=0, failed=[], chapters=[c["title"] for c in chapters])

    def one(ch):
        try:
            knowledge.build_chapter_map(con, llm, ch["id"])
        except Exception as e:  # keep going; the chapter stays in the pending list and can be retried
            with _lock:
                _state["failed"].append({"title": ch["title"], "error": str(e)[:200]})
        finally:
            with _lock:
                _state["done"] += 1

    def run():
        try:
            with ThreadPoolExecutor(max_workers=workers) as pool:
                list(pool.map(one, chapters))
        finally:
            with _lock:
                _state["running"] = False

    threading.Thread(target=run, daemon=True, name="repair").start()
    with _lock:
        return dict(_state)
