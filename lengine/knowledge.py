"""Knowledge Mapping: per chapter, extract concepts, formulas, cases, confusions and prerequisites.

Pattern taken from Math-To-Manim's staged chain: one JSON artifact per stage, validated before the
next stage uses it (every concept must cite pages inside its chapter), so a degenerate reply fails
loudly instead of silently producing an empty map.
"""
from __future__ import annotations

import re
import sqlite3

from . import ingest
from .db import j, rows
from .llm import LLM, complete_json

KINDS = {"memory", "concept", "procedure", "design"}
FORMS = {"parametric", "process", "structure", "timeline", "derivation", "code", "case", "definition"}

SYSTEM = """You build a knowledge map of ONE textbook chapter for a learner.
Use ONLY the chapter text given. Every concept must cite the page numbers [p.N] where the text supports it.
Return JSON: {"concepts":[{
 "key": "snake_case_id", "name": "...", "summary": "2-3 sentences in the textbook's own terms",
 "kind": "memory|concept|procedure|design",
 "form": "parametric|process|structure|timeline|derivation|code|case|definition",
 "formulas": ["..."], "examples": ["case or example from the text"],
 "confusions": ["a misconception or easily confused pair"],
 "requires": ["keys of prerequisite concepts in this list"], "pages": [N, ...]}]}
kind: memory = facts/terms to recall; concept = an idea to understand; procedure = steps to carry out;
design = analysis/judgement applied to new situations.
form: parametric = a relationship between quantities that can be varied (e.g. a curve that shifts);
process = a sequence or causal chain; structure = parts and their relations; timeline = events in time;
derivation = a formula or proof; case = learned through worked cases; definition = a term.
Produce 8-15 concepts, ordered so prerequisites come first. Write names and summaries in {lang}."""


def _numbered(chunks: list[dict], limit_chars: int = 90000) -> str:
    out, size = [], 0
    for c in chunks:
        piece = f"[p.{c['page']}] {c['text']}"
        size += len(piece)
        if size > limit_chars:
            break
        out.append(piece)
    return "\n".join(out)


def validate(data: dict, page_start: int, page_end: int) -> list[dict]:
    concepts = data.get("concepts") or []
    if len(concepts) < 3:
        raise ValueError(f"knowledge map too small: {len(concepts)} concepts")
    keys = set()
    clean = []
    for c in concepts:
        key = re.sub(r"[^a-z0-9_]", "_", str(c.get("key", "")).lower()).strip("_")
        pages = [int(p) for p in c.get("pages", []) if str(p).isdigit() and page_start <= int(p) <= page_end]
        if not key or not c.get("name") or not pages:
            continue  # uncited concepts are dropped: the map must stay traceable to the book
        c.update(key=key, pages=sorted(set(pages)),
                 kind=c.get("kind") if c.get("kind") in KINDS else "concept",
                 form=c.get("form") if c.get("form") in FORMS else "definition")
        keys.add(key)
        clean.append(c)
    for c in clean:
        c["requires"] = [r for r in c.get("requires", []) if r in keys and r != c["key"]]
    return clean


def _has_history(con, concept_id: int) -> bool:
    return any(con.execute(sql, (concept_id,)).fetchone() for sql in (
        "SELECT 1 FROM attempts WHERE concept_id=? LIMIT 1", "SELECT 1 FROM cards WHERE concept_id=?",
        "SELECT 1 FROM sessions WHERE concept_id=? LIMIT 1", "SELECT 1 FROM artifacts WHERE concept_id=? LIMIT 1"))


def build_chapter_map(con: sqlite3.Connection, llm: LLM, chapter_id: int, lang: str = "Chinese") -> list[dict]:
    """Build (or rebuild) one chapter's map.

    Rebuilding never loses learning history: an old concept with attempts, a review card, a lesson or a
    visual is kept (and reused when the new map has a concept of the same name); old concepts without
    history are replaced by the new map.
    """
    ch = dict(con.execute("SELECT * FROM chapters WHERE id=?", (chapter_id,)).fetchone())
    text = _numbered(ingest.chapter_text(con, chapter_id))
    data = complete_json(llm, SYSTEM.replace("{lang}", lang),
                         f"Chapter: {ch['title']} (pages {ch['page_start']}-{ch['page_end']})\n\n{text}",
                         purpose="knowledge", book_id=ch["book_id"])
    concepts = validate(data, ch["page_start"], ch["page_end"])
    old = rows(con.execute("SELECT id, key, name FROM concepts WHERE chapter_id=?", (chapter_id,)))
    keep = {o["id"]: o for o in old if _has_history(con, o["id"])}
    new_keys = {c["key"] for c in concepts}
    # prerequisites never cross chapters at this stage; edges that do came from the old key collision bug
    con.execute("DELETE FROM prerequisites WHERE concept_id IN (SELECT id FROM concepts WHERE chapter_id=?) "
                "OR requires_id IN (SELECT id FROM concepts WHERE chapter_id=?)", (chapter_id, chapter_id))
    for o in old:
        if o["id"] in keep:
            continue
        con.execute("DELETE FROM questions WHERE concept_id=?", (o["id"],))
        con.execute("DELETE FROM concepts WHERE id=?", (o["id"],))
    by_name = {o["name"]: o for o in keep.values()}
    for c in concepts:  # reuse a kept concept of the same name so its history stays attached
        o = by_name.get(c["name"])
        if o and o["key"] != c["key"] and o["key"] not in new_keys:
            con.execute("UPDATE concepts SET key=? WHERE id=?", (c["key"], o["id"]))
    ids = {}
    for c in concepts:
        cur = con.execute(
            "INSERT INTO concepts(book_id, chapter_id, key, name, kind, form, summary, formulas, examples, confusions, pages)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(chapter_id,key) DO UPDATE SET name=excluded.name, kind=excluded.kind,"
            " form=excluded.form, summary=excluded.summary, formulas=excluded.formulas, examples=excluded.examples,"
            " confusions=excluded.confusions, pages=excluded.pages RETURNING id",
            (ch["book_id"], chapter_id, c["key"], c["name"], c["kind"], c["form"], c.get("summary", ""),
             j(c.get("formulas", [])), j(c.get("examples", [])), j(c.get("confusions", [])), j(c["pages"])))
        ids[c["key"]] = cur.fetchone()[0]
    for c in concepts:
        for r in c["requires"]:
            con.execute("INSERT OR IGNORE INTO prerequisites VALUES (?,?)", (ids[c["key"]], ids[r]))
    con.execute("UPDATE chapters SET map_built_at=datetime('now') WHERE id=?", (chapter_id,))
    con.commit()
    return concepts


def chapters_needing_repair(con: sqlite3.Connection, book_id: int | None = None) -> list[dict]:
    """Chapters damaged by the pre-2.0 key collision: a concept of one chapter was merged into another
    chapter's concept with the same key, which shows up as prerequisites that cross chapters."""
    sql = ("SELECT DISTINCT ch.id, ch.book_id, ch.ord, ch.title FROM prerequisites p "
           "JOIN concepts a ON a.id=p.concept_id JOIN concepts b ON b.id=p.requires_id "
           "JOIN chapters ch ON ch.id IN (a.chapter_id, b.chapter_id) WHERE a.chapter_id != b.chapter_id")
    out = rows(con.execute(sql))
    return sorted((c for c in out if book_id is None or c["book_id"] == book_id), key=lambda c: (c["book_id"], c["ord"]))


def learning_path(con: sqlite3.Connection, book_id: int, chapter_id: int | None = None) -> list[dict]:
    """Topological order over prerequisites (ties broken by first cited page)."""
    sql = "SELECT * FROM concepts WHERE book_id=?" + (" AND chapter_id=?" if chapter_id else "")
    cs = {c["id"]: c for c in rows(con.execute(sql, (book_id, chapter_id) if chapter_id else (book_id,)))}
    req = {cid: set() for cid in cs}
    for r in con.execute("SELECT * FROM prerequisites"):
        if r["concept_id"] in cs and r["requires_id"] in cs:
            req[r["concept_id"]].add(r["requires_id"])
    import json
    order, done = [], set()
    while len(order) < len(cs):
        ready = [cid for cid in cs if cid not in done and req[cid] <= done]
        if not ready:  # cycle: fall back to page order
            ready = [cid for cid in cs if cid not in done]
        ready.sort(key=lambda cid: (json.loads(cs[cid]["pages"]) or [0])[0])
        order.append(cs[ready[0]])
        done.add(ready[0])
    return order
