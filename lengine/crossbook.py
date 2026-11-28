"""Cross-book questions: hybrid retrieval over every book, answers that keep each author's view apart.

Retrieval mixes two rankings with reciprocal rank fusion:
- vector: the question's nearest concepts (concept summaries are in Chinese for every book, so this also
  reaches English books), each turned into the best passage on the concept's own pages;
- full text: the trigram index over all chunks (exact terms, names, numbers).
The model sees numbered passages [S1].. and may cite only those. Each view it writes must carry a quote
copied from a cited passage; the program checks the quote is really there, and a view whose evidence does
not check out is kept but marked 证据不足 with its citations removed. Book, author, chapter and page in the
answer come from the database, never from the model.
"""
from __future__ import annotations

import json

from . import embed, ingest, relations
from .db import j, rows
from .llm import LLM, complete_json

MAX_PASSAGES = 14
PER_BOOK = 5
RRF_K = 60

SYSTEM = """You answer a learner's question across the books in their library, using ONLY the numbered passages.
Each passage header gives book, author, chapter and page. Cite passages by their ids, e.g. "S3".
Rules:
- First, for each book that has relevant passages, state what THAT author says, in their own terms. Never merge
  two authors into one opinion. If authors define a term differently, say so explicitly.
- Then give your own synthesis (clearly marked as AI synthesis): common ground, differences in method,
  where one book complements another, real conflicts, and when each view applies.
- Every item needs "cites" (passage ids) and a "quote" copied EXACTLY from one cited passage (8-80 characters).
- If the passages do not answer part of the question, say so in "gaps" instead of using outside knowledge.
Return JSON: {"by_book": [{"book": "title", "views": [{"text": "...", "cites": ["S1"], "quote": "..."}]}],
 "synthesis": [{"text": "...", "cites": ["S2","S5"], "quote": "..."}],
 "gaps": "what the passages do not cover, or empty"}
Write in Chinese."""


def _books(con, book_ids: list[int] | None) -> dict[int, dict]:
    bs = {r["id"]: r for r in rows(con.execute("SELECT id, title, author FROM books"))}
    return {k: v for k, v in bs.items() if not book_ids or k in book_ids}


def retrieve(con, question: str, book_ids: list[int] | None = None, embedder=None) -> list[dict]:
    books = _books(con, book_ids)
    ranked: list[list[int]] = []
    meta: dict[int, dict] = {}
    # vector ranking through concepts
    try:
        qv = (embedder or embed.get_embedder()).query(question)
    except Exception:
        qv = None
    if qv is not None:
        allowed = {r[0] for r in con.execute(
            f"SELECT id FROM concepts WHERE book_id IN ({','.join('?' * len(books))})", tuple(books))} if books else set()
        concepts = relations._concepts(con, allowed)
        vec_list = []
        for cid, sim in embed.nearest(con, "concept", qv, k=24, allowed=allowed):
            p = relations.passage(con, concepts[cid])
            if p and p["chunk_id"] not in vec_list:
                vec_list.append(p["chunk_id"])
                meta.setdefault(p["chunk_id"], {"concept": concepts[cid]["name"], "similarity": round(sim, 3)})
        ranked.append(vec_list)
    # full-text ranking, per book so a long book does not crowd out the others
    fts: list[int] = []
    for bid in books:
        fts += [h["id"] for h in ingest.search(con, bid, question, k=6)]
    ranked.append(fts)
    score: dict[int, float] = {}
    for lst in ranked:
        for rank, cid in enumerate(lst):
            score[cid] = score.get(cid, 0) + 1 / (RRF_K + rank + 1)
    chosen, per_book = [], {}
    hit_books = {r[0] for r in con.execute(
        f"SELECT DISTINCT book_id FROM chunks WHERE id IN ({','.join('?' * len(score))})", tuple(score))} if score else set()
    cap = max(PER_BOOK, MAX_PASSAGES // max(1, len(hit_books)))  # one book: all slots; several: shared fairly
    for cid in sorted(score, key=lambda c: -score[c]):
        r = con.execute("SELECT c.id, c.book_id, c.page, c.text, c.section, ch.title chapter FROM chunks c "
                        "LEFT JOIN chapters ch ON ch.id=c.chapter_id WHERE c.id=?", (cid,)).fetchone()
        if not r or r["book_id"] not in books or per_book.get(r["book_id"], 0) >= cap:
            continue
        per_book[r["book_id"]] = per_book.get(r["book_id"], 0) + 1
        b = books[r["book_id"]]
        chosen.append({"chunk_id": r["id"], "book_id": r["book_id"], "book": b["title"], "author": b["author"],
                       "chapter": r["chapter"], "section": r["section"], "page": r["page"], "text": r["text"][:1100],
                       "score": round(score[cid], 4), **meta.get(cid, {})})
        if len(chosen) >= MAX_PASSAGES:
            break
    for i, p in enumerate(chosen, start=1):
        p["id"] = f"S{i}"
    return chosen


def _check(items, by_id: dict[str, dict]) -> list[dict]:
    out = []
    for it in items or []:
        if not isinstance(it, dict) or not str(it.get("text", "")).strip():
            continue
        cites = [c for c in (it.get("cites") or []) if c in by_id]
        quote = str(it.get("quote") or "")
        ok = any(relations.quote_in(quote, by_id[c]["text"]) for c in cites)
        out.append({"text": str(it["text"]).strip(), "cites": cites if ok else [], "quote": quote if ok else "",
                    "verified": ok})
    return out


def answer(con, llm: LLM, question: str, book_ids: list[int] | None = None, embedder=None) -> dict:
    passages = retrieve(con, question, book_ids, embedder)
    if not passages:
        return {"question": question, "passages": [], "by_book": [], "synthesis": [],
                "gaps": "知识库里没有找到和这个问题相关的原文。", "stats": {"views": 0, "verified": 0}}
    blocks = []
    for p in passages:
        who = f"《{p['book']}》" + (f" {p['author']}" if p["author"] else "")
        blocks.append(f"[{p['id']}] {who} | {p['chapter'] or ''} | p.{p['page']}\n{p['text']}")
    # relations already found between the concepts behind these passages (AI-judged, with checked quotes)
    rel_lines = _relations_context(con, passages)
    user = f"Question: {question}\n\nPassages:\n\n" + "\n\n".join(blocks)
    if rel_lines:
        user += "\n\nRelations found earlier between concepts in these books (for orientation only, cite passages):\n" + "\n".join(rel_lines)
    data = complete_json(llm, SYSTEM, user, "crossbook", book_id=passages[0]["book_id"])
    by_id = {p["id"]: p for p in passages}
    by_book = []
    for b in data.get("by_book") or []:
        views = _check(b.get("views"), by_id)
        if views:
            by_book.append({"book": str(b.get("book", "")), "views": views})
    synthesis = _check(data.get("synthesis"), by_id)
    items = [v for b in by_book for v in b["views"]] + synthesis
    result = {"question": question, "passages": passages, "by_book": by_book, "synthesis": synthesis,
              "gaps": str(data.get("gaps") or ""), "stats": {"views": len(items), "verified": sum(v["verified"] for v in items)}}
    con.execute("INSERT INTO questions_crossbook(question, book_ids, answer) VALUES (?,?,?)",
                (question, j(book_ids or []), json.dumps(result, ensure_ascii=False)))
    con.commit()
    return result


def _relations_context(con, passages: list[dict]) -> list[str]:
    chunk_ids = [p["chunk_id"] for p in passages]
    if not chunk_ids:
        return []
    q = ",".join("?" * len(chunk_ids))
    pages = {(r["book_id"], r["page"]) for r in con.execute(f"SELECT book_id, page FROM chunks WHERE id IN ({q})", chunk_ids)}
    cids = [c["id"] for c in rows(con.execute("SELECT id, book_id, pages FROM concepts"))
            if any((c["book_id"], p) in pages for p in json.loads(c["pages"] or "[]"))]
    if not cids:
        return []
    qc = ",".join("?" * len(cids))
    out = []
    for r in rows(con.execute(
            f"SELECT r.type, r.rationale, s.name sn, t.name tn FROM relations r JOIN concepts s ON s.id=r.source_id "
            f"JOIN concepts t ON t.id=r.target_id WHERE r.source_id IN ({qc}) AND r.target_id IN ({qc}) LIMIT 12",
            (*cids, *cids))):
        out.append(f"- {r['sn']} —{relations.TYPES.get(r['type'], r['type'])}→ {r['tn']}: {r['rationale']}")
    return out


def history(con, limit: int = 20) -> list[dict]:
    return [{"id": r["id"], "question": r["question"], "created_at": r["created_at"]}
            for r in con.execute("SELECT id, question, created_at FROM questions_crossbook ORDER BY id DESC LIMIT ?", (limit,))]


def get(con, qid: int) -> dict | None:
    r = con.execute("SELECT answer FROM questions_crossbook WHERE id=?", (qid,)).fetchone()
    return json.loads(r["answer"]) if r else None
