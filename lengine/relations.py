"""Evidence-backed relations between concepts, within a book (across chapters) and across books.

Two steps, as planned in the 2.0 audit:
1. Candidates: for each concept, its nearest concepts by local embedding (free). Same-chapter pairs are
   left to the chapter map's own prerequisites.
2. Judgement: the model reads both concepts with a passage of original text for each and picks one of
   seven relation types, or none. It must quote both passages; the program checks that each quote really
   is in the passage it was given, and a pair whose quotes do not check out is discarded. Pages come from
   the passages, never from the model. Different authors' views are kept side by side, never merged.

Every judged pair is remembered in relation_checks (including "none"), so nothing is paid for twice.
"""
from __future__ import annotations

import re
from collections import defaultdict

from . import embed
from .db import rows
from .llm import LLM, complete_json

TYPES = {
    "similar_to": "相似概念", "supports": "相互支持", "contradicts": "观点冲突", "extends": "理论延伸",
    "prerequisite": "前置知识", "applies_to": "实际应用", "example_of": "案例说明",
}
SYMMETRIC = {"similar_to", "supports", "contradicts"}
SIM_MIN = 0.60          # cosine floor within a book (Qwen3-Embedding; below this pairs were mostly unrelated in 思考，快与慢)
SIM_MIN_CROSS = 0.55    # across books, where wording and language differ more
PER_CONCEPT = 4         # candidates kept per concept (cross-book first)
BATCH = 6               # pairs per model call
CONF_MIN = 0.6
PASSAGE_CHARS = 700

SYSTEM = """You compare pairs of concepts from books a learner is studying. For each pair you get, for A and B:
the book, author, chapter, a short summary, and one passage of the ORIGINAL text.
Decide whether the original texts support one of these relations from A to B:
- similar_to: A and B name the same or a closely similar idea (possibly different words or languages)
- supports: A's claim or evidence backs B's claim
- contradicts: A and B make claims that conflict (different authors may disagree: say how)
- extends: A develops, generalises or refines B
- prerequisite: A must be understood before B
- applies_to: A (a principle/method) is applied in B (a domain, decision or situation)
- example_of: A is a case or example that illustrates B
- none: related topic only, or the passages do not show the relation
Rules:
- Judge ONLY from the passages and summaries given. Do not use outside knowledge about the books.
- quote_a and quote_b must be copied EXACTLY from passage A and passage B (8-60 characters each),
  choosing the words that show the relation. If you cannot quote both, answer none.
- Keep each author's view separate: the rationale says what each side says, never a merged opinion. In the rationale
  call the two concepts by their names (never "A" or "B"), and name the books when they differ.
- confidence: 0.9 = the passages state it plainly; 0.7 = clear but implicit; below 0.6 = guess (answer none instead).
Return JSON: {"pairs":[{"id": N, "relation": "...", "direction": "A->B" or "B->A",
 "rationale": "1-2 sentences in Chinese", "confidence": 0.0-1.0, "quote_a": "...", "quote_b": "..."}]}"""


def _norm(s: str) -> str:
    return re.sub(r"[\s­]+", "", s or "").lower()


def quote_in(quote: str, passage: str) -> bool:
    q = _norm(quote).strip("…。.,，\"'“”")
    return len(q) >= 6 and q in _norm(passage)


def _concepts(con, ids: set[int] | None = None) -> dict[int, dict]:
    sql = ("SELECT c.id, c.name, c.summary, c.pages, c.book_id, c.chapter_id, b.title book, b.author, ch.title chapter "
           "FROM concepts c JOIN books b ON b.id=c.book_id LEFT JOIN chapters ch ON ch.id=c.chapter_id")
    out = {r["id"]: r for r in rows(con.execute(sql))}
    return {k: v for k, v in out.items() if ids is None or k in ids}


def candidates(con, book_ids: list[int]) -> list[tuple[int, int, float]]:
    """Unjudged pairs (a < b) where at least one concept is in book_ids. Cross-book pairs are preferred."""
    allc = _concepts(con)
    scope = [c for c in allc.values() if c["book_id"] in set(book_ids)]
    checked = {(r[0], r[1]) for r in con.execute("SELECT a, b FROM relation_checks")}
    existing = {(min(r[0], r[1]), max(r[0], r[1])) for r in con.execute("SELECT source_id, target_id FROM relations")}
    pairs: dict[tuple[int, int], float] = {}
    for c in scope:
        v = embed.vector_of(con, "concept", c["id"])
        if v is None:
            continue
        near = [(oid, s) for oid, s in embed.nearest(con, "concept", v, k=30)
                if oid != c["id"] and oid in allc and allc[oid]["chapter_id"] != c["chapter_id"]
                and s >= (SIM_MIN if allc[oid]["book_id"] == c["book_id"] else SIM_MIN_CROSS)]
        cross = [x for x in near if allc[x[0]]["book_id"] != c["book_id"]][:PER_CONCEPT]
        same = [x for x in near if allc[x[0]]["book_id"] == c["book_id"]][:max(1, PER_CONCEPT - len(cross))]
        for oid, s in cross + same:
            key = (min(c["id"], oid), max(c["id"], oid))
            if key not in checked and key not in existing:
                pairs[key] = max(pairs.get(key, 0), s)
    return sorted(((a, b, s) for (a, b), s in pairs.items()), key=lambda x: -x[2])


def _overlap(a: str, b: str) -> int:
    """Shared character bigrams (works for Chinese and English without a tokenizer)."""
    grams = lambda t: {t[i:i + 2] for i in range(len(t) - 1)}
    return len(grams(_norm(a)) & grams(_norm(b)))


def passage(con, c: dict) -> dict | None:
    """The chunk on the concept's own pages that shares most words with the concept's name and summary."""
    import json
    pages = json.loads(c["pages"] or "[]")
    chunks = rows(con.execute(
        f"SELECT id, page, text FROM chunks WHERE chapter_id=? AND page IN ({','.join('?' * len(pages))})",
        (c["chapter_id"], *pages))) if pages else []
    if not chunks:
        return None
    probe = f"{c['name']} {c['name']} {c['summary'] or ''}"
    best = max(chunks, key=lambda ch: _overlap(probe, ch["text"]))
    text = best["text"]
    if len(text) > PASSAGE_CHARS:  # keep the window that overlaps the concept most
        step = PASSAGE_CHARS // 3
        starts = range(0, max(1, len(text) - PASSAGE_CHARS + step), step)
        i = max(starts, key=lambda k: _overlap(probe, text[k:k + PASSAGE_CHARS]))
        text = text[i:i + PASSAGE_CHARS]
    return {"chunk_id": best["id"], "page": best["page"], "text": text}


def _side(c: dict, p: dict, label: str) -> str:
    who = f"《{c['book']}》" + (f"（{c['author']}）" if c.get("author") else "")
    return (f"{label}: {c['name']} | {who} | {c['chapter'] or ''}\nsummary: {c['summary'] or ''}\n"
            f"passage {label} [p.{p['page']}]: {p['text']}")


WORKERS = 5


def judge(con, llm: LLM, pairs: list[tuple[int, int, float]], progress=None) -> dict:
    """Asks the model about each pair (several calls in parallel); stores verified relations.
    Database reads and writes stay on the calling thread; only the model calls run in the pool."""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    allc = _concepts(con, {x for a, b, _ in pairs for x in (a, b)})
    model = getattr(llm, "model_for", lambda _p: None)("relations")
    stats: dict[str, int] = defaultdict(int)
    batches = []
    for i in range(0, len(pairs), BATCH):
        items, ctx = [], {}
        for n, (a, b, s) in enumerate(pairs[i:i + BATCH], start=1):
            ca, cb = allc.get(a), allc.get(b)
            pa, pb = (passage(con, ca), passage(con, cb)) if ca and cb else (None, None)
            if not (pa and pb):
                con.execute("INSERT OR REPLACE INTO relation_checks(a, b, result) VALUES (?,?,?)", (a, b, "no_text"))
                stats["no_text"] += 1
                continue
            ctx[n] = (a, b, s, ca, cb, pa, pb)
            items.append(f"### Pair {n}\n{_side(ca, pa, 'A')}\n\n{_side(cb, pb, 'B')}")
        if items:
            batches.append((ctx, "\n\n".join(items)))
    con.commit()
    done = 0
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futs = {pool.submit(complete_json, llm, SYSTEM, text, "relations", 2,
                            next(iter(ctx.values()))[3]["book_id"]): ctx for ctx, text in batches}
        for f in as_completed(futs):
            ctx = futs[f]
            try:
                data = f.result()
            except Exception:
                data = {}
            answered = set()
            for r in data.get("pairs") or []:
                try:
                    n = int(r.get("id"))
                except (TypeError, ValueError):
                    continue
                if n in ctx and n not in answered:
                    answered.add(n)
                    stats[_store(con, ctx[n], r, model)] += 1
            for n in set(ctx) - answered:  # not answered: left unchecked so a later run can try again
                stats["no_answer"] += 1
            con.commit()
            done += len(ctx)
            if progress:
                progress(done, len(pairs))
    return dict(stats)


def _store(con, ctx, r: dict, model: str | None) -> str:
    a, b, sim, ca, cb, pa, pb = ctx
    rel = str(r.get("relation", "none")).strip().lower()
    try:
        conf = float(r.get("confidence") or 0)
    except (TypeError, ValueError):
        conf = 0.0
    if rel not in TYPES:
        result = "none"
    elif conf < CONF_MIN:
        result = "low_confidence"
    elif not (quote_in(r.get("quote_a", ""), pa["text"]) and quote_in(r.get("quote_b", ""), pb["text"])):
        result = "unverified"  # a quote not found in its passage: the claim has no checked evidence
    else:
        result = rel
    con.execute("INSERT OR REPLACE INTO relation_checks(a, b, result) VALUES (?,?,?)", (a, b, result))
    if result not in TYPES:
        return result
    src, dst, qs, qd, ps, pd = (a, b, r["quote_a"], r["quote_b"], pa["page"], pb["page"])
    if str(r.get("direction", "A->B")).replace(" ", "").upper() == "B->A" and rel not in SYMMETRIC:
        src, dst, qs, qd, ps, pd = (b, a, r["quote_b"], r["quote_a"], pb["page"], pa["page"])
    con.execute(
        "INSERT OR REPLACE INTO relations(source_id, target_id, type, rationale, source_quote, source_page, target_quote, "
        "target_page, confidence, similarity, method, model) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (src, dst, rel, str(r.get("rationale", ""))[:500], qs.strip()[:200], ps, qd.strip()[:200], pd,
         round(conf, 2), round(sim, 3), "model", model))
    return rel


def estimate(con, book_ids: list[int], pairs: list | None = None) -> dict:
    """Model cost of judging the current candidates (Haiku 5.5 list prices)."""
    from .llm import ClaudeLLM, cost_usd
    import os
    n = len(pairs if pairs is not None else candidates(con, book_ids))
    calls = -(-n // BATCH)
    # measured on 思考，快与慢 (180 pairs, 2026-11-28): ~1,750 input and ~370 output tokens per pair
    per_in, per_out = 900 + BATCH * 1700, BATCH * 380
    tin, tout = calls * per_in, calls * per_out
    model = os.environ.get("LLM_MODEL_RELATIONS", ClaudeLLM.PURPOSE_DEFAULTS.get("relations", os.environ.get("LLM_MODEL", "claude-haiku-5-5")))
    usd = calls * (cost_usd(model, per_in, per_out) or 0.0)  # priced per call, not on the total
    return {"pairs": n, "calls": calls, "input_tokens": tin, "output_tokens": tout, "model": model, "usd": round(usd, 2)}


# ---- reading relations back -----------------------------------------------------------------------
_REL_SQL = (
    "SELECT r.*, s.name source_name, s.book_id source_book, t.name target_name, t.book_id target_book, "
    "bs.title source_book_title, bt.title target_book_title, bs.author source_author, bt.author target_author, "
    "cs.title source_chapter, ct.title target_chapter "
    "FROM relations r JOIN concepts s ON s.id=r.source_id JOIN concepts t ON t.id=r.target_id "
    "JOIN books bs ON bs.id=s.book_id JOIN books bt ON bt.id=t.book_id "
    "LEFT JOIN chapters cs ON cs.id=s.chapter_id LEFT JOIN chapters ct ON ct.id=t.chapter_id ")


def _label(rs: list[dict]) -> list[dict]:
    for r in rs:
        r["label"] = TYPES.get(r["type"], r["type"])
        r["cross_book"] = r["source_book"] != r["target_book"]
    return rs


def for_book(con, book_id: int) -> list[dict]:
    return _label(rows(con.execute(_REL_SQL + "WHERE s.book_id=? OR t.book_id=? ORDER BY (s.book_id != t.book_id) DESC, "
                                   "r.confidence DESC", (book_id, book_id))))


def for_concept(con, concept_id: int) -> list[dict]:
    return _label(rows(con.execute(_REL_SQL + "WHERE r.source_id=? OR r.target_id=? ORDER BY r.confidence DESC",
                                   (concept_id, concept_id))))


def stats(con) -> dict:
    by_type = {r[0]: r[1] for r in con.execute("SELECT type, count(*) FROM relations GROUP BY type")}
    checks = {r[0]: r[1] for r in con.execute("SELECT result, count(*) FROM relation_checks GROUP BY result")}
    cross = con.execute("SELECT count(*) FROM relations r JOIN concepts s ON s.id=r.source_id JOIN concepts t ON t.id=r.target_id "
                        "WHERE s.book_id != t.book_id").fetchone()[0]
    return {"by_type": by_type, "checks": checks, "cross_book": cross, "total": sum(by_type.values())}
