"""Background processing pipeline: one job per imported book, 12 stages with their own status.

    1 upload  2 validate  3 extract  4 structure  5 segment  6 chunk      (free, seconds)
    7 knowledge  8 classify                                                (model calls, parallel per chapter)
    9 embed  10 graph  11 materials                                        (Phase 4 / on demand)
    12 complete (quality checks)

Design (cross-checked against Huey/RQ-style queues, see the 2.0 audit): job and stage state live in
SQLite, so a page reload or a restart picks up where processing stopped. Work runs on threads inside the
web process — no separate worker to start on Windows. Stages 1-6 always run; the model stages wait for
the learner to confirm the cost estimate unless the job was started with auto_map. Chapters are mapped
in parallel; a failed chapter is retried on its own and never restarts the book. Each chapter becomes
studyable as soon as its own map is done.
"""
from __future__ import annotations

import json
import logging
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed

from pypdf import PdfReader

from . import ingest, knowledge
from .db import j, rows

log = logging.getLogger("lengine.pipeline")

STAGES = [
    ("upload", "文件上传"), ("validate", "PDF 校验"), ("extract", "文本提取"), ("structure", "结构识别"),
    ("segment", "章节切分"), ("chunk", "内容分块"), ("knowledge", "知识提取"), ("classify", "知识分类与去重"),
    ("embed", "向量化"), ("graph", "知识图谱整合"), ("materials", "学习材料"), ("complete", "处理完成"),
]
LATER = {"embed": "Phase 4 开发中", "graph": "Phase 4 开发中", "materials": "学习时按需生成"}
MAP_WORKERS = 5
MAX_ATTEMPTS = 3
RETRY_DELAY = 2.0  # seconds, grows with each attempt

# Cost model measured on 思考，快与慢 (Sonnet 5.5 knowledge maps, 2026-11-27): input tokens ≈ CJK characters
# + other characters / 4, plus ~1.2k tokens of instructions; ~2.7k output tokens per chapter.
SONNET_IN, SONNET_OUT = 2.0, 10.0


def estimate_tokens(text: str) -> int:
    cjk = sum(1 for ch in text if "㐀" <= ch <= "鿿")
    return int(cjk + (len(text) - cjk) / 4)


def estimate_map_cost(con, book_id: int, chapter_ids: list[int] | None = None) -> dict:
    sql = "SELECT id FROM chapters WHERE book_id=? AND map_status IN ('pending','failed')"
    ids = chapter_ids or [r["id"] for r in con.execute(sql, (book_id,))]
    tin = tout = 0
    for cid in ids:
        text = "".join(r["text"] for r in con.execute("SELECT text FROM chunks WHERE chapter_id=?", (cid,)))
        tin += min(estimate_tokens(text), 90000) + 1200
        tout += 2700
    return {"chapters": len(ids), "input_tokens": tin, "output_tokens": tout,
            "usd": round((tin * SONNET_IN + tout * SONNET_OUT) / 1e6, 2)}


class Runner:
    """Runs queued jobs one at a time (chapters inside a job run in parallel)."""

    def __init__(self, con, llm_factory):
        self.con, self.llm_factory = con, llm_factory
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    # ---- public API -------------------------------------------------------------------------------
    def start(self) -> None:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            # a job interrupted by a restart continues from its last unfinished stage
            self.con.execute("UPDATE jobs SET status='queued' WHERE status='running'")
            self.con.execute("UPDATE job_stages SET status='pending' WHERE status='running'")
            self.con.execute("UPDATE chapters SET map_status='pending' WHERE map_status='running'")
            self.con.commit()
            self._thread = threading.Thread(target=self._loop, daemon=True, name="pipeline")
            self._thread.start()

    def create_job(self, book_id: int, options: dict) -> int:
        jid = self.con.execute("INSERT INTO jobs(book_id, status, stage, options) VALUES (?,?,?,?)",
                               (book_id, "queued", "validate", j(options))).lastrowid
        self.con.executemany("INSERT INTO job_stages(job_id, stage, ord, status) VALUES (?,?,?,?)",
                             [(jid, s, i, "done" if s == "upload" else "pending") for i, (s, _) in enumerate(STAGES)])
        self.con.commit()
        self.kick()
        return jid

    def confirm_map(self, job_id: int, chapter_ids: list[int] | None = None) -> None:
        """The learner accepted the estimate: run the model stages (optionally for chosen chapters only)."""
        job = self._job(job_id)
        if chapter_ids:
            self.con.execute(f"UPDATE chapters SET map_status='pending', map_error=NULL WHERE book_id=? AND id IN "
                             f"({','.join('?' * len(chapter_ids))})", (job["book_id"], *chapter_ids))
        else:
            self.con.execute("UPDATE chapters SET map_status='pending', map_error=NULL, map_attempts=0 "
                             "WHERE book_id=? AND map_status='failed'", (job["book_id"],))
        opts = json.loads(job["options"] or "{}")
        opts["map_confirmed"] = True
        for s in ("knowledge", "classify", "complete"):
            self._stage(job_id, s, status="pending", error=None)
        self.con.execute("UPDATE jobs SET status='queued', options=?, error=NULL, updated_at=datetime('now') WHERE id=?",
                         (j(opts), job_id))
        self.con.commit()
        self.kick()

    def retry(self, job_id: int) -> None:
        """Re-runs failed stages and failed chapters only; finished chapters are never redone."""
        self.con.execute("UPDATE job_stages SET status='pending', error=NULL WHERE job_id=? AND status='failed'", (job_id,))
        book = self._job(job_id)["book_id"]
        if self.con.execute("SELECT 1 FROM chapters WHERE book_id=? AND map_status='failed'", (book,)).fetchone():
            self.con.execute("UPDATE job_stages SET status='pending', error=NULL WHERE job_id=? "
                             "AND stage IN ('knowledge','classify','complete')", (job_id,))
        self.con.execute("UPDATE chapters SET map_status='pending', map_error=NULL, map_attempts=0 "
                         "WHERE book_id=? AND map_status='failed'", (book,))
        self.con.execute("UPDATE jobs SET status='queued', error=NULL, updated_at=datetime('now') WHERE id=?", (job_id,))
        self.con.commit()
        self.kick()

    def reprocess(self, book_id: int, reocr: bool = False, force: bool = False) -> int:
        """Rebuilds a book from its PDF with the current import code (old imports, fixed structure rules)."""
        if self.con.execute("SELECT 1 FROM jobs WHERE book_id=? AND status IN ('queued','running')", (book_id,)).fetchone():
            raise ValueError("这本书正在处理中")
        n = learning_history(self.con, book_id)
        if n and not force:
            raise ValueError(f"这本书已有 {n} 条学习记录，重新处理会删除它们")
        self.con.execute("UPDATE jobs SET status='superseded' WHERE book_id=? AND status IN ('waiting','failed','partial')", (book_id,))
        clear_book(self.con, book_id, keep_pages=not reocr)
        self.con.execute("UPDATE books SET status='processing', updated_at=datetime('now') WHERE id=?", (book_id,))
        self.con.commit()
        return self.create_job(book_id, {"reprocess": True})

    def kick(self) -> None:
        self._wake.set()

    # ---- loop -------------------------------------------------------------------------------------
    def _loop(self) -> None:
        while True:
            job = self.con.execute("SELECT * FROM jobs WHERE status='queued' ORDER BY id LIMIT 1").fetchone()
            if not job:
                self._wake.wait(timeout=30)
                self._wake.clear()
                continue
            try:
                self._run(dict(job))
            except Exception as e:  # recorded on the job; never kills the loop
                log.exception("job %s failed", job["id"])
                self.con.execute("UPDATE jobs SET status='failed', error=?, updated_at=datetime('now') WHERE id=?",
                                 (f"{type(e).__name__}: {e}"[:500], job["id"]))
                self.con.commit()

    def _job(self, job_id: int) -> dict:
        return dict(self.con.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone())

    def _stage(self, job_id: int, stage: str, **fields) -> None:
        if fields.get("status") == "running":
            fields.setdefault("started_at", time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()))
        if fields.get("status") in ("done", "failed", "skipped"):
            fields.setdefault("finished_at", time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()))
        sets = ", ".join(f"{k}=?" for k in fields)
        self.con.execute(f"UPDATE job_stages SET {sets} WHERE job_id=? AND stage=?", (*fields.values(), job_id, stage))
        if fields.get("status") == "running":
            self.con.execute("UPDATE jobs SET stage=?, updated_at=datetime('now') WHERE id=?", (stage, job_id))
        self.con.commit()

    def _status(self, job_id: int, stage: str) -> str:
        return self.con.execute("SELECT status FROM job_stages WHERE job_id=? AND stage=?", (job_id, stage)).fetchone()[0]

    def _run(self, job: dict) -> None:
        jid, book_id = job["id"], job["book_id"]
        opts = json.loads(job["options"] or "{}")
        self.con.execute("UPDATE jobs SET status='running', updated_at=datetime('now') WHERE id=?", (jid,))
        self.con.commit()
        book = dict(self.con.execute("SELECT * FROM books WHERE id=?", (book_id,)).fetchone())
        path = book["source_path"]

        def step(stage, fn):
            if self._status(jid, stage) in ("done", "skipped"):
                return True
            self._stage(jid, stage, status="running", error=None, note=None)
            try:
                note = fn()
                self._stage(jid, stage, status="done", note=note)
                return True
            except Exception as e:
                self._stage(jid, stage, status="failed", error=f"{type(e).__name__}: {e}"[:500])
                log.error("stage %s of job %s: %s", stage, jid, traceback.format_exc())
                self.con.execute("UPDATE jobs SET status='failed', error=?, updated_at=datetime('now') WHERE id=?",
                                 (f"{dict(STAGES)[stage]}失败：{e}"[:500], jid))
                self.con.commit()
                return False

        state: dict = {}

        def validate():
            import pypdfium2 as pdfium
            pdf = pdfium.PdfDocument(path)  # raises on a corrupt or password-protected file
            n = len(pdf)
            pdf.close()
            if n == 0:
                raise ValueError("PDF 没有页面")
            self.con.execute("UPDATE books SET page_count=?, status='processing' WHERE id=?", (n, book_id))
            return f"{n} 页"

        def extract():
            if self.con.execute("SELECT count(*) FROM pages WHERE book_id=?", (book_id,)).fetchone()[0]:
                return "已提取（复用）"
            def progress(done, total):
                if done % 10 == 0 or done == total:
                    self._stage(jid, "extract", done=done, total=total)
            pages = ingest.extract_pages(path, progress=progress)
            self.con.executemany("INSERT OR REPLACE INTO pages(book_id, page, text, method) VALUES (?,?,?,?)",
                                 [(book_id, i + 1, t, m) for i, (t, m) in enumerate(pages)])
            self.con.commit()
            ocr = sum(m == "ocr" for _, m in pages)
            return f"{len(pages)} 页" + (f"，其中 {ocr} 页用 OCR 识别" if ocr else "，全部为文字层，未使用 OCR")

        def page_texts():
            if "pages" not in state:
                state["pages"] = [(r["text"], r["method"]) for r in
                                  self.con.execute("SELECT text, method FROM pages WHERE book_id=? ORDER BY page", (book_id,))]
            return state["pages"]

        def structure():
            state["chapters"] = ingest.detect_structure(PdfReader(path), [t for t, _ in page_texts()])
            body = sum(c.kind == "body" for c in state["chapters"])
            return f"{len(state['chapters'])} 个部分，其中正文 {body} 章"

        def segment_and_chunk():
            if self.con.execute("SELECT count(*) FROM chapters WHERE book_id=?", (book_id,)).fetchone()[0]:
                return "已切分（复用）"
            if "chapters" not in state:
                structure()
            ingest.write_chapters(self.con, book_id, state["chapters"], [t for t, _ in page_texts()])
            return f"{len(state['chapters'])} 章"

        for stage, fn in (("validate", validate), ("extract", extract), ("structure", structure)):
            if not step(stage, fn):
                return
        if not step("segment", segment_and_chunk):
            return
        n_chunks = self.con.execute("SELECT count(*) FROM chunks WHERE book_id=?", (book_id,)).fetchone()[0]
        step("chunk", lambda: f"{n_chunks} 个文本块，保留页码和小节标题")

        est = estimate_map_cost(self.con, book_id)
        self.con.execute("UPDATE jobs SET estimate_usd=? WHERE id=?", (est["usd"], jid))
        if not (opts.get("auto_map") or opts.get("map_confirmed")):
            self._stage(jid, "knowledge", status="waiting", note=f"等待确认：{est['chapters']} 章，预计 ${est['usd']:.2f}")
            self.con.execute("UPDATE jobs SET status='waiting', stage='knowledge', updated_at=datetime('now') WHERE id=?", (jid,))
            self.con.execute("UPDATE books SET status='ready' WHERE id=?", (book_id,))
            self.con.commit()
            return

        if not step("knowledge", lambda: self._map_chapters(jid, book_id)):
            return
        step("classify", lambda: self._classify(book_id))
        for s, note in LATER.items():
            if self._status(jid, s) == "pending":
                self._stage(jid, s, status="skipped", note=note)
        report = quality_report(self.con, book_id)
        self.con.execute("UPDATE jobs SET report=? WHERE id=?", (j(report), jid))
        step("complete", lambda: f"质量检查：{len(report['issues'])} 项提示")
        failed = self.con.execute("SELECT count(*) FROM chapters WHERE book_id=? AND map_status='failed'", (book_id,)).fetchone()[0]
        self.con.execute("UPDATE jobs SET status=?, finished_at=datetime('now'), updated_at=datetime('now'), error=? WHERE id=?",
                         ("partial" if failed else "done", f"{failed} 章知识提取失败，可单独重试" if failed else None, jid))
        self.con.execute("UPDATE books SET status='ready', updated_at=datetime('now') WHERE id=?", (book_id,))
        self.con.commit()

    # ---- stages with real work --------------------------------------------------------------------
    def _map_chapters(self, jid: int, book_id: int) -> str:
        todo = rows(self.con.execute("SELECT id, title FROM chapters WHERE book_id=? AND map_status='pending' ORDER BY ord", (book_id,)))
        planned = self.con.execute("SELECT count(*) FROM chapters WHERE book_id=? AND map_status NOT IN ('not_planned')",
                                   (book_id,)).fetchone()[0]
        done = lambda: self.con.execute("SELECT count(*) FROM chapters WHERE book_id=? AND map_status='done'", (book_id,)).fetchone()[0]
        self._stage(jid, "knowledge", done=done(), total=planned)
        if not todo:
            return f"{done()} / {planned} 章"
        llm = self.llm_factory()

        def one(ch):
            self.con.execute("UPDATE chapters SET map_status='running', map_attempts=map_attempts+1 WHERE id=?", (ch["id"],))
            self.con.commit()
            last = None
            for attempt in range(MAX_ATTEMPTS):
                try:
                    knowledge.build_chapter_map(self.con, llm, ch["id"])
                    self.con.execute("UPDATE chapters SET map_status='done', map_error=NULL WHERE id=?", (ch["id"],))
                    self.con.commit()
                    return True
                except Exception as e:
                    last = e
                    if attempt + 1 < MAX_ATTEMPTS:
                        time.sleep(RETRY_DELAY * (attempt + 1))
            self.con.execute("UPDATE chapters SET map_status='failed', map_error=? WHERE id=?", (str(last)[:300], ch["id"]))
            self.con.commit()
            return False

        with ThreadPoolExecutor(max_workers=MAP_WORKERS) as pool:
            for f in as_completed([pool.submit(one, ch) for ch in todo]):
                f.result()
                self._stage(jid, "knowledge", done=done())
        failed = self.con.execute("SELECT count(*) FROM chapters WHERE book_id=? AND map_status='failed'", (book_id,)).fetchone()[0]
        if failed == len(todo):
            raise RuntimeError(f"全部 {failed} 章失败，请检查 API Key 或网络")
        return f"{done()} / {planned} 章" + (f"，{failed} 章失败可重试" if failed else "")

    def _classify(self, book_id: int) -> str:
        dups = duplicate_concepts(self.con, book_id)
        return f"知识点类型已校验；发现 {len(dups)} 组可能重复的知识点" if dups else "知识点类型已校验，未发现重复"


def learning_history(con, book_id: int) -> int:
    """Attempts, review cards, lessons and visuals on this book's concepts: never thrown away silently."""
    q = "SELECT count(*) FROM {t} WHERE concept_id IN (SELECT id FROM concepts WHERE book_id=?)"
    return sum(con.execute(q.format(t=t), (book_id,)).fetchone()[0] for t in ("attempts", "cards", "sessions", "artifacts"))


def clear_book(con, book_id: int, keep_pages: bool = True) -> None:
    """Removes everything derived from the PDF (structure, chunks, concepts, links) so it can be rebuilt."""
    concept_ids = "SELECT id FROM concepts WHERE book_id=?"
    con.execute("DELETE FROM chunks_fts WHERE rowid IN (SELECT id FROM chunks WHERE book_id=?)", (book_id,))
    con.execute("DELETE FROM vectors WHERE kind='chunk' AND owner_id IN (SELECT id FROM chunks WHERE book_id=?)", (book_id,))
    con.execute(f"DELETE FROM vectors WHERE kind='concept' AND owner_id IN ({concept_ids})", (book_id,))
    for t, cols in (("prerequisites", ("concept_id", "requires_id")), ("relations", ("source_id", "target_id")),
                    ("relation_checks", ("a", "b"))):
        for col in cols:
            con.execute(f"DELETE FROM {t} WHERE {col} IN ({concept_ids})", (book_id,))
    for t in ("questions", "attempts", "cards", "sessions", "artifacts"):
        con.execute(f"DELETE FROM {t} WHERE concept_id IN ({concept_ids})", (book_id,))
    for t in ("concepts", "chunks", "chapters"):
        con.execute(f"DELETE FROM {t} WHERE book_id=?", (book_id,))
    if not keep_pages:
        con.execute("DELETE FROM pages WHERE book_id=?", (book_id,))
    con.commit()


def duplicate_concepts(con, book_id: int) -> list[list[dict]]:
    """Same (normalised) name in different chapters: flagged for review, never merged automatically."""
    import re
    groups: dict[str, list[dict]] = {}
    for c in rows(con.execute("SELECT c.id, c.name, ch.title chapter FROM concepts c JOIN chapters ch ON ch.id=c.chapter_id "
                              "WHERE c.book_id=?", (book_id,))):
        key = re.sub(r"[\s（）()“”\"'：:，,、—\-]", "", c["name"]).lower()
        groups.setdefault(key, []).append(c)
    return [g for g in groups.values() if len({c["chapter"] for c in g}) > 1]


def quality_report(con, book_id: int) -> dict:
    """Basic checks after processing: page coverage, OCR, empty chapters, citations, duplicates."""
    issues: list[dict] = []
    chs = rows(con.execute("SELECT * FROM chapters WHERE book_id=? ORDER BY page_start", (book_id,)))
    n_pages = con.execute("SELECT page_count FROM books WHERE id=?", (book_id,)).fetchone()[0] or 0
    for a, b in zip(chs, chs[1:]):
        if b["page_start"] > a["page_end"] + 1:
            issues.append({"level": "warn", "text": f"第 {a['page_end'] + 1}–{b['page_start'] - 1} 页不属于任何章节"})
        if b["page_start"] <= a["page_end"]:
            issues.append({"level": "warn", "text": f"「{a['title']}」与「{b['title']}」页码重叠"})
    if chs and chs[0]["page_start"] > 1:
        issues.append({"level": "info", "text": f"第 1–{chs[0]['page_start'] - 1} 页是封面或目录，未纳入章节"})
    empty_pages = con.execute("SELECT count(*) FROM pages WHERE book_id=? AND length(trim(text)) < 15", (book_id,)).fetchone()[0]
    if n_pages and empty_pages > n_pages * 0.1:
        issues.append({"level": "warn", "text": f"{empty_pages} 页几乎没有文字，可能是扫描页且 OCR 未能识别"})
    for ch in chs:
        if ch["map_status"] == "done" and not con.execute("SELECT 1 FROM concepts WHERE chapter_id=?", (ch["id"],)).fetchone():
            issues.append({"level": "warn", "text": f"「{ch['title']}」没有提取到知识点"})
        if ch["map_status"] == "failed":
            issues.append({"level": "error", "text": f"「{ch['title']}」知识提取失败：{ch['map_error'] or ''}"[:200]})
    bad = con.execute("SELECT count(*) FROM concepts c JOIN chapters ch ON ch.id=c.chapter_id WHERE c.book_id=? "
                      "AND (c.pages IS NULL OR c.pages='[]')", (book_id,)).fetchone()[0]
    if bad:
        issues.append({"level": "warn", "text": f"{bad} 个知识点没有原文页码"})
    dups = duplicate_concepts(con, book_id)
    if dups:
        issues.append({"level": "info", "text": f"{len(dups)} 组同名知识点出现在不同章节（保留，供你判断是否同一概念）",
                       "items": [[f"{c['name']}（{c['chapter']}）" for c in g] for g in dups[:20]]})
    kinds: dict[str, int] = {}
    for ch in chs:
        kinds[ch["kind"] or "body"] = kinds.get(ch["kind"] or "body", 0) + 1
    return {"issues": issues, "chapters": len(chs), "kinds": kinds, "pages": n_pages,
            "ocr_pages": con.execute("SELECT count(*) FROM pages WHERE book_id=? AND method='ocr'", (book_id,)).fetchone()[0]}


def job_view(con, job_id: int) -> dict:
    job = dict(con.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone())
    stages = rows(con.execute("SELECT stage, ord, status, done, total, note, error, started_at, finished_at FROM job_stages "
                              "WHERE job_id=? ORDER BY ord", (job_id,)))
    labels = dict(STAGES)
    for s in stages:
        s["label"] = labels.get(s["stage"], s["stage"])
    chapters = rows(con.execute("SELECT id, title, kind, map_status, map_error, page_start, page_end FROM chapters "
                                "WHERE book_id=? ORDER BY ord", (job["book_id"],)))
    planned = [c for c in chapters if c["map_status"] not in ("not_planned", None)]
    weights = {"done": 1, "skipped": 1}
    progress = 0.0
    for s in stages:  # the model stage dominates the wall time, so it carries most of the bar
        w = 0.7 if s["stage"] == "knowledge" else 0.3 / (len(stages) - 1)
        if s["stage"] == "knowledge" and s["total"]:
            progress += w * (s["done"] / s["total"])
        else:
            progress += w * weights.get(s["status"], 0)
    concepts = con.execute("SELECT count(*) FROM concepts WHERE book_id=?", (job["book_id"],)).fetchone()[0]
    return {**job, "options": json.loads(job["options"] or "{}"), "report": json.loads(job["report"] or "{}"),
            "stages": stages, "progress": round(min(progress, 1.0), 3), "concepts": concepts,
            "chapters_total": len(planned), "chapters_done": sum(c["map_status"] == "done" for c in planned),
            "chapters_failed": [c for c in chapters if c["map_status"] == "failed"],
            "chapters": chapters}
