"""Material Hub: turn a PDF into chapters and page-tagged chunks, keeping every page number.

Text comes from pdfium (pypdfium2, BSD/Apache: ~9x faster than pypdf on a 345-page book) and scanned
pages fall back to RapidOCR (Apache-2.0); PyMuPDF (AGPL) is avoided so the engine stays free to use.
Structure comes from the PDF outline when it has one (chapters, parts, preface/appendix/back matter,
sections), else from "Chapter N / 第N章" headings.
"""
from __future__ import annotations

from pathlib import Path
import re
import sqlite3
import hashlib
from dataclasses import dataclass, field

from pypdf import PdfReader

CHAPTER_RE = re.compile(r"^\s*(chapter|第)\s*([0-9一二三四五六七八九十]+)\s*(章)?[:：.\s]+(.{2,80})$", re.I | re.M)


@dataclass
class Chapter:
    title: str
    page_start: int  # 1-based, inclusive
    page_end: int
    kind: str = "body"  # body | front | conclusion | appendix | skip
    part: str | None = None
    sections: list[tuple[str, int]] = field(default_factory=list)


PART_RE = re.compile(r"^\s*(第\s*[0-9一二三四五六七八九十]+\s*(部分|部|篇|编)|part\s+([0-9]+|[ivxlc]+)\b)", re.I)
KIND_RULES = [  # checked in order against a non-chapter title
    ("skip", re.compile(r"目录|contents|致谢|鸣谢|acknowledg|注释|注解|^notes?\b|参考文献|references|bibliography|索引|^index|版权|copyright|作者简介|about the author", re.I)),
    ("appendix", re.compile(r"附录|appendix", re.I)),
    ("conclusion", re.compile(r"结语|结论|后记|尾声|epilogue|conclusion|afterword", re.I)),
    ("front", re.compile(r"序|前言|引言|导言|导论|preface|foreword|prologue|introduction", re.I)),
]
MAP_BY_DEFAULT = {"body", "front", "conclusion"}  # appendix: on request; skip: never


def clean_title(title: str) -> str:
    """Outline titles carry NULs, soft hyphens and line breaks from the PDF."""
    return re.sub(r"\s+", " ", re.sub(r"[\x00\xad\u200b]", "", title or "")).strip()


def _is_chapter(title: str) -> bool:
    return bool(CHAPTER_RE.match(title) or re.match(r"^\d+\s", title))


def _outline(reader: PdfReader) -> list[tuple[int, str, int]]:
    """(depth, title, 1-based page) for every outline entry with a resolvable page."""
    found: list[tuple[int, str, int]] = []
    def walk(items, depth):
        for it in items:
            if isinstance(it, list):
                walk(it, depth + 1)
                continue
            try:
                found.append((depth, clean_title(it.title), reader.get_destination_page_number(it) + 1))
            except Exception:
                continue
    try:
        walk(reader.outline or [], 0)
    except Exception:
        return []
    return found


def _classify(title: str, before_first_chapter: bool) -> str:
    for kind, rx in KIND_RULES:
        if rx.search(title):
            return "front" if kind == "front" and not before_first_chapter else kind
    return "front" if before_first_chapter else "body"


def _outline_chapters(reader: PdfReader) -> list[tuple[str, int]]:
    """Kept for callers of the old API: chapter-like outline titles with pages."""
    return [(t, p) for d, t, p in _outline(reader) if d <= 1 and _is_chapter(t)]


HEADING_RE = re.compile(
    r"^\s*((?:第\s*[0-9一二三四五六七八九十百零〇两]+\s*章)|(?:chapter\s+(?:\d+|[ivxlc]+|one|two|three|four|five|six|seven|eight|nine|ten)\b))"
    r"[\s:：.、]*(.{0,60})$", re.I)


def _pattern_chapters(pages: list[str]) -> list[tuple[str, int]]:
    """Chapter headings near the top of a page. Table-of-contents pages (several headings) are ignored,
    and a heading alone on its line takes the next line as its title."""
    out: list[tuple[str, int]] = []
    seen: set[str] = set()
    for i, text in enumerate(pages, start=1):
        lines = [l.strip() for l in text.strip().splitlines() if l.strip()]
        if sum(1 for l in lines if HEADING_RE.match(l)) >= 3:
            continue  # contents page
        for j, line in enumerate(lines[:5]):
            m = HEADING_RE.match(line)
            if not m:
                continue
            key = re.sub(r"\s+", "", m.group(1)).lower()
            if key in seen:
                break
            title = m.group(2).strip()
            if not title and j + 1 < len(lines) and len(lines[j + 1]) <= 40:
                title = lines[j + 1]
            seen.add(key)
            out.append((clean_title(f"{m.group(1)} {title}"), i))
            break
    return out


def _text_tokens(text: str) -> int:
    cjk = sum(1 for ch in text if "\u3400" <= ch <= "\u9fff")
    return cjk + (len(text) - cjk) // 4


MAX_SEGMENT_TOKENS = 40000   # longer chapters are split so one model call reads all of it
WINDOW_TOKENS = 15000        # size of page windows when a book has no usable structure


def _page_windows(pages: list[str], start: int, end: int, target: int) -> list[tuple[int, int]]:
    """Consecutive page ranges of about `target` tokens each, cut at page boundaries."""
    out, s, acc = [], start, 0
    for p in range(start, end + 1):
        acc += _text_tokens(pages[p - 1])
        if acc >= target and p < end:
            out.append((s, p))
            s, acc = p + 1, 0
    out.append((s, end))
    if len(out) > 1 and sum(_text_tokens(pages[p - 1]) for p in range(*[out[-1][0], out[-1][1] + 1])) < target // 4:
        last = out.pop()
        out[-1] = (out[-1][0], last[1])  # fold a short tail into the previous window
    return out


def _split_long(chapters: list["Chapter"], pages: list[str]) -> list["Chapter"]:
    out: list[Chapter] = []
    for ch in chapters:
        tokens = sum(_text_tokens(pages[p - 1]) for p in range(ch.page_start, ch.page_end + 1))
        if tokens <= MAX_SEGMENT_TOKENS:
            out.append(ch)
            continue
        n = -(-tokens // MAX_SEGMENT_TOKENS)
        wins = _page_windows(pages, ch.page_start, ch.page_end, -(-tokens // n))
        for k, (a, b) in enumerate(wins, start=1):
            out.append(Chapter(f"{ch.title}（{k}/{len(wins)}）", a, b, ch.kind, ch.part,
                               [(t, p) for t, p in ch.sections if a <= p <= b]))
    return out


def _windows_structure(pages: list[str]) -> list["Chapter"]:
    n = len(pages)
    wins = _page_windows(pages, 1, n, WINDOW_TOKENS) if n else []
    return [Chapter(f"第 {a}–{b} 页", a, b) for a, b in wins] or [Chapter("全文", 1, max(n, 1))]


_CJK = r"\u3000-\u303f\u3400-\u9fff\uff00-\uffef"


def normalize_text(text: str) -> str:
    """pypdf breaks CJK lines after almost every token ("2001\n年\n12\n月"); rejoin them."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
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


def detect_structure(reader: PdfReader | None, pages: list[str]) -> list[Chapter]:
    """Chapters plus the book's other parts (preface, conclusion, appendices, back matter), each with a kind,
    the part it belongs to and its section headings, from the outline when there is one."""
    n = len(pages)
    entries = _outline(reader) if reader is not None else []
    chapter_entries = [e for e in entries if e[0] <= 1 and _is_chapter(e[1])]
    if not chapter_entries:
        marks = _pattern_chapters(pages)
        # a stray "Chapter 19" in running text is not a structure: require a few headings in page order
        if len(marks) < 3 and n > 40:
            return _split_long(_windows_structure(pages), pages)
        if not marks:
            return _windows_structure(pages)
        chapter_entries = [(0, t, p) for t, p in marks]
        entries = chapter_entries
    depth = min(d for d, _, _ in chapter_entries)
    first_ch = min(p for _, _, p in chapter_entries)
    segs: list[tuple[str, int, str]] = []  # (title, page, kind)
    parts: list[tuple[str, int]] = []
    for d, t, p in entries:
        if d > depth:
            continue
        if _is_chapter(t):
            segs.append((t, p, "body"))
        elif PART_RE.match(t):
            parts.append((t, p))
        elif d == depth or d == 0:
            segs.append((t, p, _classify(t, p < first_ch)))
    segs.sort(key=lambda x: x[1])
    fixed = _fix_starts([(t, p) for t, p, _ in segs], pages)
    segs = sorted(((t, p, k) for (t, p), (_, _, k) in zip(fixed, segs)), key=lambda x: x[1])
    out: list[Chapter] = []
    for i, (t, start, kind) in enumerate(segs):
        end = segs[i + 1][1] - 1 if i + 1 < len(segs) else n
        part = next((pt for pt, pp in reversed(parts) if pp <= start), None)
        out.append(Chapter(t, start, max(start, min(end, n)), kind, part if kind == "body" else None))
    for d, t, p in entries:  # sections: outline entries one level below the chapters
        if d == depth + 1:
            for ch in out:
                if ch.page_start <= p <= ch.page_end:
                    ch.sections.append((t, p))
                    break
    if out and out[0].page_start > 1:  # pages before the first entry are never dropped
        lead = "".join(pages[: out[0].page_start - 1])
        # cover, copyright and contents pages are kept (searchable) but not sent to the model
        toc = re.search(r"目\s*录|contents", lead[:3000], re.I) and out[0].page_start <= 15
        out.insert(0, Chapter(f"开头部分（第 1–{out[0].page_start - 1} 页）", 1, out[0].page_start - 1,
                              "skip" if toc or _text_tokens(lead) < 300 else "front"))
    return _split_long(out, pages)


def detect_chapters(reader: PdfReader, pages: list[str]) -> list[Chapter]:
    return detect_structure(reader, pages)


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


def extract_pages(path: str, ocr: bool = True, progress=None) -> list[tuple[str, str]]:
    """Text of every page as (text, method). Born-digital pages use pdfium's text layer (fast, no OCR).
    A page whose only text is a watermark or header repeated across the book, or that has no text at all
    but is not blank, is OCR'd with RapidOCR when it is installed."""
    import pypdfium2 as pdfium
    from collections import Counter
    pdf = pdfium.PdfDocument(path)
    try:
        texts = []
        for i in range(len(pdf)):
            page = pdf[i]
            tp = page.get_textpage()
            texts.append(tp.get_text_range().replace("\r\n", "\n"))
            tp.close()
            page.close()
        lines = Counter(l.strip() for t in texts for l in set(t.splitlines()) if len(l.strip()) >= 4)
        common = {l for l, c in lines.items() if c >= max(5, len(texts) * 0.5)}  # watermarks, running headers
        out: list[tuple[str, str]] = []
        engine = None
        for i, text in enumerate(texts):
            method = "text"
            own = "".join(l for l in text.splitlines() if l.strip() not in common)
            if ocr and len(re.sub(r"\s", "", own)) < 15:
                page = pdf[i]
                if _has_image(page, pdfium) or not _looks_blank(page):
                    engine = engine or _ocr_engine()
                    if engine is not None:
                        import numpy as np
                        res = engine(np.array(page.render(scale=2).to_pil().convert("RGB")))
                        ocr_text = "\n".join(res.txts or []) if res is not None else ""
                        if len(ocr_text.strip()) > len(own.strip()):
                            text, method = ocr_text, "ocr"
                page.close()
            out.append((text, method))
            if progress:
                progress(i + 1, len(texts))
    finally:
        pdf.close()
    return out


_OCR = None


def _has_image(page, pdfium) -> bool:
    """Only pages that carry an image can hold scanned text; blank pages skip OCR."""
    try:
        return next(page.get_objects(filter=[pdfium.raw.FPDF_PAGEOBJ_IMAGE], max_depth=2), None) is not None
    except Exception:
        return True


def _looks_blank(page) -> bool:
    """A quick low-resolution render: a page with no ink skips OCR."""
    try:
        import numpy as np
        img = np.asarray(page.render(scale=0.25, grayscale=True).to_pil())
        return float(img.std()) < 2.0
    except Exception:
        return False


def _ocr_engine():
    global _OCR
    if _OCR is None:
        try:
            import logging
            from rapidocr import RapidOCR
            _OCR = RapidOCR()
            logging.getLogger("RapidOCR").setLevel(logging.ERROR)
        except Exception:
            _OCR = False
    return _OCR or None


def write_chapters(con: sqlite3.Connection, book_id: int, chapters: list[Chapter], texts: list[str]) -> None:
    """Chapter rows plus page-tagged chunks (with section titles) for the full-text index."""
    for ord_, ch in enumerate(chapters):
        ch_id = con.execute(
            "INSERT INTO chapters(book_id, ord, title, page_start, page_end, kind, part, map_status) VALUES (?,?,?,?,?,?,?,?)",
            (book_id, ord_, ch.title, ch.page_start, ch.page_end, ch.kind, ch.part,
             "pending" if ch.kind in MAP_BY_DEFAULT else "not_planned")).lastrowid
        n = 0
        for page_no in range(ch.page_start, ch.page_end + 1):
            section = next((t for t, p in reversed(ch.sections) if p <= page_no), None)
            for piece in chunk_page(normalize_text(texts[page_no - 1])):
                h = hashlib.sha1(piece.encode("utf-8")).hexdigest()
                cid = con.execute(
                    "INSERT INTO chunks(book_id, chapter_id, page, ord, text, section, content_hash) VALUES (?,?,?,?,?,?,?)",
                    (book_id, ch_id, page_no, n, piece, section, h)).lastrowid
                con.execute("INSERT INTO chunks_fts(rowid, text) VALUES (?,?)", (cid, piece))
                n += 1
    con.commit()


def write_book(con: sqlite3.Connection, book_id: int, reader: PdfReader | None, pages: list[tuple[str, str]]) -> list[Chapter]:
    """Pages, chapters and page-tagged chunks for an already created book row."""
    texts = [t for t, _ in pages]
    con.executemany("INSERT OR REPLACE INTO pages(book_id, page, text, method) VALUES (?,?,?,?)",
                    [(book_id, i + 1, t, m) for i, (t, m) in enumerate(pages)])
    con.execute("UPDATE books SET page_count=? WHERE id=?", (len(pages), book_id))
    chapters = detect_structure(reader, texts)
    write_chapters(con, book_id, chapters, texts)
    return chapters


def ingest_pdf(con: sqlite3.Connection, path: str, title: str | None = None, domain: str = "general",
               license: str | None = None) -> int:
    """Synchronous import (scripts and tests). The web app runs the same steps as a background job."""
    reader = PdfReader(path)
    book_id = con.execute("INSERT INTO books(title, source_path, domain, license) VALUES (?,?,?,?)",
                          (title or Path(path).stem, path, domain, license)).lastrowid
    write_book(con, book_id, reader, extract_pages(path))
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
