"""Local embeddings for concepts and text chunks (free, runs on the CPU, nothing leaves the computer).

Model: Qwen3-Embedding-0.6B int8 via fastembed (Apache-2.0, multilingual, so Chinese and English books
align). Chosen in the 2.0 audit over bge-small-zh (Chinese only) and jina-v3 (non-commercial licence).
Vectors are L2-normalised float32 blobs in SQLite; search is brute-force cosine in numpy, which takes a few
milliseconds for tens of thousands of vectors, so no vector database is needed.
"""
from __future__ import annotations

import hashlib
import os
import threading
from pathlib import Path

import numpy as np

MODEL = os.environ.get("EMBED_MODEL", "Qwen/Qwen3-Embedding-0.6B-Q")
DIM = 1024
QUERY_TASK = "Given a question or a concept, retrieve passages and concepts from books that discuss it"
MAX_CHARS = 1500  # chunks are ≤1200 characters; long texts are cut to keep CPU time predictable

_model = None
_lock = threading.Lock()


class Embedder:
    """Wraps fastembed; tests substitute a deterministic fake with the same two methods."""

    def __init__(self, cache_dir: str | None = None):
        from fastembed import TextEmbedding
        cache = cache_dir or os.environ.get("FASTEMBED_CACHE") or str(Path(os.environ.get("ENGINE_DATA", "data")) / "models")
        self.model = TextEmbedding(MODEL, cache_dir=cache)

    def passages(self, texts: list[str]) -> np.ndarray:
        return np.array(list(self.model.embed([t[:MAX_CHARS] for t in texts], batch_size=16)), dtype=np.float32)

    def query(self, text: str) -> np.ndarray:
        # Qwen3-Embedding: instruction prefix on queries only, documents are embedded as they are
        return np.array(list(self.model.embed([f"Instruct: {QUERY_TASK}\nQuery:{text}"]))[0], dtype=np.float32)


def get_embedder():
    global _model
    with _lock:
        if _model is None:
            _model = Embedder()
        return _model


def set_embedder(e) -> None:
    global _model, _cache
    _model = e
    _cache = {}


def concept_text(c: dict) -> str:
    return f"{c['name']}：{c.get('summary') or ''}"


def _hash(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]


def index_book(con, book_id: int, progress=None, embedder=None) -> dict:
    """Embeds the book's concepts and chunks that are new or changed. Returns counts."""
    emb = embedder or get_embedder()
    todo: list[tuple[str, int, str]] = []
    for c in con.execute("SELECT id, name, summary FROM concepts WHERE book_id=?", (book_id,)):
        todo.append(("concept", c["id"], concept_text(dict(c))))
    for ch in con.execute("SELECT id, text, section FROM chunks WHERE book_id=?", (book_id,)):
        todo.append(("chunk", ch["id"], (f"{ch['section']}\n" if ch["section"] else "") + ch["text"]))
    have = {(r[0], r[1]): r[2] for r in con.execute(
        "SELECT kind, owner_id, hash FROM vectors WHERE model=? AND owner_id IN (SELECT id FROM concepts WHERE book_id=?) AND kind='concept' "
        "UNION ALL SELECT kind, owner_id, hash FROM vectors WHERE model=? AND kind='chunk' AND owner_id IN (SELECT id FROM chunks WHERE book_id=?)",
        (MODEL, book_id, MODEL, book_id))}
    todo = [t for t in todo if have.get((t[0], t[1])) != _hash(t[2])]
    batch = 64
    for i in range(0, len(todo), batch):
        part = todo[i:i + batch]
        vecs = emb.passages([t[2] for t in part])
        con.executemany("INSERT OR REPLACE INTO vectors(kind, owner_id, model, hash, vec) VALUES (?,?,?,?,?)",
                        [(k, oid, MODEL, _hash(text), v.astype(np.float32).tobytes()) for (k, oid, text), v in zip(part, vecs)])
        con.commit()
        if progress:
            progress(min(i + batch, len(todo)), len(todo))
    _cache.clear()
    return {"embedded": len(todo)}


# ---- search ---------------------------------------------------------------------------------------
_cache: dict = {}


def matrix(con, kind: str) -> tuple[np.ndarray, np.ndarray]:
    """(ids, normalised vectors) for all items of a kind; cached until the row count changes."""
    n = con.execute("SELECT count(*) FROM vectors WHERE kind=? AND model=?", (kind, MODEL)).fetchone()[0]
    hit = _cache.get(kind)
    if hit and hit[0] == n:
        return hit[1], hit[2]
    ids, vecs = [], []
    for r in con.execute("SELECT owner_id, vec FROM vectors WHERE kind=? AND model=? ORDER BY owner_id", (kind, MODEL)):
        ids.append(r[0])
        vecs.append(np.frombuffer(r[1], dtype=np.float32))
    ids_a = np.array(ids, dtype=np.int64)
    mat = np.vstack(vecs) if vecs else np.zeros((0, DIM), dtype=np.float32)
    _cache[kind] = (n, ids_a, mat)
    return ids_a, mat


def nearest(con, kind: str, vec: np.ndarray, k: int = 10, allowed: set[int] | None = None) -> list[tuple[int, float]]:
    ids, mat = matrix(con, kind)
    if not len(ids):
        return []
    sims = mat @ vec.astype(np.float32)
    order = np.argsort(-sims)
    out = []
    for i in order:
        oid = int(ids[i])
        if allowed is not None and oid not in allowed:
            continue
        out.append((oid, float(sims[i])))
        if len(out) >= k:
            break
    return out


def vector_of(con, kind: str, owner_id: int) -> np.ndarray | None:
    r = con.execute("SELECT vec FROM vectors WHERE kind=? AND owner_id=? AND model=?", (kind, owner_id, MODEL)).fetchone()
    return np.frombuffer(r[0], dtype=np.float32) if r else None
