"""Material Hub: turn a PDF into chapters and page-tagged chunks, keeping every page number.

PDF text extraction uses pypdf (BSD) rather than PyMuPDF (AGPL), so the engine stays free to use
commercially. Chapters come from the PDF outline when it has one, else from heading patterns.
"""
from __future__ import annotations

from pathlib import Path
import re
import sqlite3
from dataclasses import dataclass

from pypdf import PdfReader

CHAPTER_RE = re.compile(r"^\s*(chapter|第)\s*([0-9一二三四五六七八九十]+)\s*(章)?[:：.\s]+(.{2,80})$", re.I | re.M)


@dataclass
class Chapter:
    title: str
    page_start: int  # 1-based, inclusive
    page_end: int


def _outline_chapters(reader: PdfReader) -> list[tuple[str, int]]:
    found: list[tuple[str, int]] = []
    def walk(items, depth):
        for it in items:
            if isinstance(it, list):
                walk(it, depth + 1)
                continue
            try:
                page = reader.get_destination_page_number(it) + 1
            except Exception:
                continue
            if depth <= 1:
                found.append((it.title.strip(), page))
    walk(reader.outline or [], 0)
    chap = [(t, p) for t, p in found if CHAPTER_RE.match(t) or re.match(r"^\d+\s", t)]
    return chap or [(t, p) for t, p in found if p > 1]


def _pattern_chapters(pages: list[str]) -> list[tuple[str, int]]:
    out = []
    for i, text in enumerate(pages, start=1):
        head = "\n".join(text.strip().splitlines()[:4])
        m = CHAPTER_RE.search(head)
        if m:
            out.append((m.group(0).strip(), i))
    return out


_CJK = r"\u3000-\u303f\u3400-\u9fff\uff00-\uffef"


def normalize_text(text: str) -> str:
    """pypdf breaks CJK lines after almost every token ("2001\n年\n12\n月"); rejoin them."""
    text = re.sub(rf"(?<=[{_CJK}])[ \t]*\n[ \t]*|[ \t]*\n[ \t]*(?=[{_CJK}])", "", text)
    return re.sub(r"[ \t]*\n[ \t]*", " ", text) if re.search(rf"[{_CJK}]", text) else text


def _fix_starts(marks: list[tuple[str, int]], pages: list[str]) -> list[tuple[str, int]]:
    """Outlines are sometimes off (two chapters pointing at one page). If a chapter's number heading
    is not on its outline page, look for it before the next chapter's page."""
    fixed = []
    for idx, (title, start) in enumerate(marks):
        m = re.match(r"^\s*(?:chapter|第)\s*(\d+)", title, re.I)
        nxt = marks[idx + 1][1] if idx + 1 < len(marks) else len(pages)
        if m:
            pat = re.compile(rf"^(?:chapter|第)\s*{m.group(1)}(?!\d)", re.I)
            heads = {p: re.sub(r"\s+", "", pages[p - 1][:40]) for p in range(start, min(nxt, len(pages)) + 1)}
            if not pat.match(heads.get(start, "")):
                start = next((p for p, h in heads.items() if pat.match(h)), start)
        fixed.append((title, start))
    return fixed


def detect_chapters(reader: PdfReader, pages: list[str]) -> list[Chapter]:
    marks = _outline_chapters(reader) or _pattern_chapters(pages)
    if not marks:
        return [Chapter("Full text", 1, len(pages))]
    marks.sort(key=lambda x: x[1])
    marks = sorted(_fix_starts(marks, pages), key=lambda x: x[1])
    chapters = []
    for idx, (title, start) in enumerate(marks):
        end = marks[idx + 1][1] - 1 if idx + 1 < len(marks) else len(pages)
        chapters.append(Chapter(title, start, max(start, end)))
    return chapters


def chunk_page(text: str, max_chars: int = 1200) -> list[str]:
    paras = [p.strip() for p in re.split(r"\n\s*\n|(?<=[.!?。！？])\n", text) if p.strip()]
    chunks, cur = [], ""
    for p in paras:
        if len(cur) + len(p) > max_chars and cur:
            chunks.append(cur)
            cur = p
        else:
            cur = f"{cur}\n{p}" if cur else p
    if cur:
        chunks.append(cur)
    return chunks


def ingest_pdf(con: sqlite3.Connection, path: str, title: str | None = None, domain: str = "general",
               license: str | None = None) -> int:
    reader = PdfReader(path)
    pages = [(p.extract_text() or "") for p in reader.pages]
    cur = con.execute("INSERT INTO books(title, source_path, domain, license) VALUES (?,?,?,?)",
                      (title or Path(path).stem, path, domain, license))
    book_id = cur.lastrowid
    for ord_, ch in enumerate(detect_chapters(reader, pages)):
        ch_id = con.execute(
            "INSERT INTO chapters(book_id, ord, title, page_start, page_end) VALUES (?,?,?,?,?)",
            (book_id, ord_, ch.title, ch.page_start, ch.page_end)).lastrowid
        n = 0
        for page_no in range(ch.page_start, ch.page_end + 1):
            for piece in chunk_page(normalize_text(pages[page_no - 1])):
                cid = con.execute(
                    "INSERT INTO chunks(book_id, chapter_id, page, ord, text) VALUES (?,?,?,?,?)",
                    (book_id, ch_id, page_no, n, piece)).lastrowid
                con.execute("INSERT INTO chunks_fts(rowid, text) VALUES (?,?)", (cid, piece))
                n += 1
    con.commit()
    return book_id


def search(con: sqlite3.Connection, book_id: int, query: str, k: int = 6,
           chapter_id: int | None = None) -> list[dict]:
    """Full-text search; every hit carries its page so answers can cite the textbook."""
    terms = []
    for t in re.findall(r"\w+", query.lower()):
        if re.search(rf"[{_CJK}]", t):  # trigram index: CJK runs have no spaces, so query by 3-grams
            terms += [t[i:i + 3] for i in range(max(1, len(t) - 2))] if len(t) >= 3 else []
        elif len(t) > 2:
            terms.append(t)
    terms = list(dict.fromkeys(terms))
    if not terms:
        return []
    fts = " OR ".join(f'"{t}"' for t in terms[:12])
    sql = ("SELECT c.id, c.page, c.chapter_id, c.text, bm25(chunks_fts) AS score FROM chunks_fts "
           "JOIN chunks c ON c.id = chunks_fts.rowid WHERE chunks_fts MATCH ? AND c.book_id = ?")
    args: list = [fts, book_id]
    if chapter_id:
        sql += " AND c.chapter_id = ?"
        args.append(chapter_id)
    sql += " ORDER BY score LIMIT ?"
    args.append(k)
    return [dict(r) for r in con.execute(sql, args)]


def chapter_text(con: sqlite3.Connection, chapter_id: int) -> list[dict]:
    return [dict(r) for r in con.execute(
        "SELECT page, text FROM chunks WHERE chapter_id = ? ORDER BY page, ord", (chapter_id,))]
