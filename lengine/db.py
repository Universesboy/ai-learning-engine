"""SQLite storage. One file holds every book, the knowledge map and the learner's history.

Schema changes are numbered migrations tracked in `PRAGMA user_version`. Each runs once, only adds
tables/columns (never drops learner data), and an existing database is copied to
`engine.db.bak-<timestamp>` before the first migration touches it.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime
from pathlib import Path

BASE_SCHEMA = """
CREATE TABLE IF NOT EXISTS books (
  id INTEGER PRIMARY KEY, title TEXT NOT NULL, source_path TEXT, domain TEXT DEFAULT 'general',
  license TEXT, created_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS chapters (
  id INTEGER PRIMARY KEY, book_id INTEGER NOT NULL REFERENCES books(id), ord INTEGER NOT NULL,
  title TEXT NOT NULL, page_start INTEGER, page_end INTEGER, status TEXT DEFAULT 'new'
);
CREATE TABLE IF NOT EXISTS chunks (
  id INTEGER PRIMARY KEY, book_id INTEGER NOT NULL, chapter_id INTEGER, page INTEGER NOT NULL,
  ord INTEGER NOT NULL, text TEXT NOT NULL
);
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(text, content='chunks', content_rowid='id', tokenize='trigram');
CREATE TABLE IF NOT EXISTS concepts (
  id INTEGER PRIMARY KEY, book_id INTEGER NOT NULL, chapter_id INTEGER, key TEXT NOT NULL,
  name TEXT NOT NULL, kind TEXT NOT NULL, form TEXT NOT NULL, summary TEXT,
  formulas TEXT DEFAULT '[]', examples TEXT DEFAULT '[]', confusions TEXT DEFAULT '[]',
  pages TEXT DEFAULT '[]', UNIQUE(book_id, key)
);
CREATE TABLE IF NOT EXISTS prerequisites (
  concept_id INTEGER NOT NULL, requires_id INTEGER NOT NULL, PRIMARY KEY (concept_id, requires_id)
);
CREATE TABLE IF NOT EXISTS questions (
  id INTEGER PRIMARY KEY, concept_id INTEGER NOT NULL, qtype TEXT NOT NULL, level TEXT NOT NULL,
  prompt TEXT NOT NULL, choices TEXT, answer TEXT, rubric TEXT, pages TEXT DEFAULT '[]',
  created_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS attempts (
  id INTEGER PRIMARY KEY, question_id INTEGER NOT NULL, concept_id INTEGER NOT NULL,
  response TEXT, score REAL NOT NULL, error_type TEXT, feedback TEXT,
  created_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS cards (
  concept_id INTEGER PRIMARY KEY, state TEXT NOT NULL, due TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
  id INTEGER PRIMARY KEY, book_id INTEGER, chapter_id INTEGER, concept_id INTEGER,
  step TEXT, transcript TEXT DEFAULT '[]', started_at TEXT DEFAULT (datetime('now')), updated_at TEXT
);
CREATE TABLE IF NOT EXISTS artifacts (
  id INTEGER PRIMARY KEY, concept_id INTEGER, kind TEXT NOT NULL, path TEXT, meta TEXT DEFAULT '{}',
  created_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS llm_usage (
  id INTEGER PRIMARY KEY, purpose TEXT, model TEXT, prompt_tokens INTEGER, completion_tokens INTEGER,
  seconds REAL, created_at TEXT DEFAULT (datetime('now'))
);
"""


def _script(con, sql: str) -> None:
    """Run several statements inside the caller's transaction (executescript would commit it)."""
    for stmt in sql.split(";"):
        if stmt.strip():
            con.execute(stmt)


def _add_column(con, table: str, column: str, decl: str) -> None:
    cols = {r[1] for r in con.execute(f"PRAGMA table_info({table})")}
    if column not in cols:
        con.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")


def _m1_book_metadata_and_chapter_scoped_keys(con) -> None:
    """2.0 foundations.

    - concepts were UNIQUE(book_id, key): when two chapters produced the same key, the later chapter's
      concept was silently merged into the earlier one. Rebuild the table as UNIQUE(chapter_id, key),
      keeping every row and id.
    - book metadata (author, category, file hash for de-duplication, page count, cover),
      chapter kind/summary fields, usage per book.
    """
    _script(con, """
    CREATE TABLE concepts_v1 (
      id INTEGER PRIMARY KEY, book_id INTEGER NOT NULL, chapter_id INTEGER, key TEXT NOT NULL,
      name TEXT NOT NULL, kind TEXT NOT NULL, form TEXT NOT NULL, summary TEXT,
      formulas TEXT DEFAULT '[]', examples TEXT DEFAULT '[]', confusions TEXT DEFAULT '[]',
      pages TEXT DEFAULT '[]', quote TEXT, importance INTEGER DEFAULT 2,
      created_at TEXT DEFAULT (datetime('now')), UNIQUE(chapter_id, key)
    );
    INSERT INTO concepts_v1(id, book_id, chapter_id, key, name, kind, form, summary, formulas, examples, confusions, pages)
      SELECT id, book_id, chapter_id, key, name, kind, form, summary, formulas, examples, confusions, pages FROM concepts;
    DROP TABLE concepts;
    ALTER TABLE concepts_v1 RENAME TO concepts;
    CREATE INDEX IF NOT EXISTS idx_concepts_book ON concepts(book_id);
    CREATE INDEX IF NOT EXISTS idx_concepts_chapter ON concepts(chapter_id);
    CREATE INDEX IF NOT EXISTS idx_chunks_chapter ON chunks(chapter_id);
    CREATE INDEX IF NOT EXISTS idx_attempts_concept ON attempts(concept_id);
    CREATE INDEX IF NOT EXISTS idx_questions_concept ON questions(concept_id);
    """)
    for col, decl in [("author", "TEXT"), ("category", "TEXT"), ("file_sha256", "TEXT"), ("page_count", "INTEGER"),
                      ("cover_path", "TEXT"), ("status", "TEXT DEFAULT 'ready'"), ("updated_at", "TEXT")]:
        _add_column(con, "books", col, decl)
    for col, decl in [("kind", "TEXT DEFAULT 'body'"), ("summary", "TEXT"), ("core_ideas", "TEXT DEFAULT '[]'"),
                      ("arguments", "TEXT DEFAULT '[]'"), ("map_built_at", "TEXT")]:
        _add_column(con, "chapters", col, decl)
    _add_column(con, "llm_usage", "book_id", "INTEGER")
    # Chapters that already have concepts were mapped before this migration.
    con.execute("UPDATE chapters SET map_built_at = datetime('now') WHERE map_built_at IS NULL AND id IN "
                "(SELECT DISTINCT chapter_id FROM concepts)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_books_sha ON books(file_sha256)")


MIGRATIONS = [_m1_book_metadata_and_chapter_scoped_keys]
SCHEMA_VERSION = len(MIGRATIONS)


def _backup(con: sqlite3.Connection, path: Path, version: int) -> Path | None:
    has_data = con.execute("SELECT count(*) FROM sqlite_master WHERE type='table' AND name='books'").fetchone()[0]
    if not has_data:
        return None
    dest = path.with_name(f"{path.name}.bak-v{version}-{datetime.now():%Y%m%d-%H%M%S}")
    with sqlite3.connect(str(dest)) as out:
        con.backup(out)
    return dest


def migrate(con: sqlite3.Connection, path: Path | None = None) -> int:
    version = con.execute("PRAGMA user_version").fetchone()[0]
    if version >= SCHEMA_VERSION:
        return version
    if path is not None:
        _backup(con, path, version)
    con.executescript(BASE_SCHEMA)
    for i in range(version, SCHEMA_VERSION):
        con.execute("BEGIN")
        try:
            MIGRATIONS[i](con)
            con.execute(f"PRAGMA user_version = {i + 1}")
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise
    return SCHEMA_VERSION


def _open(path: str | Path) -> sqlite3.Connection:
    con = sqlite3.connect(str(path), check_same_thread=False, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    if str(path) != ":memory:":
        con.execute("PRAGMA journal_mode = WAL")
        con.execute("PRAGMA synchronous = NORMAL")
    con.execute("PRAGMA busy_timeout = 30000")
    return con


def connect(path: str | Path = "data/engine.db") -> sqlite3.Connection:
    """A single connection with the schema migrated (scripts and tests)."""
    if str(path) != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    con = _open(path)
    con.isolation_level = None  # migrations manage their own transactions
    migrate(con, None if str(path) == ":memory:" else Path(path))
    con.isolation_level = ""
    return con


class ThreadLocalConnection:
    """One sqlite connection per thread, behind the same interface as a Connection.

    The web server answers requests on a thread pool and background jobs run on their own threads;
    sharing one connection across them is not safe. Modules keep calling `con.execute(...)`.
    """

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._local = threading.local()
        connect(self.path).close()  # migrate once, up front

    def _con(self) -> sqlite3.Connection:
        c = getattr(self._local, "con", None)
        if c is None:
            c = self._local.con = _open(self.path)
        return c

    def __getattr__(self, name):
        return getattr(self._con(), name)


def rows(cur) -> list[dict]:
    return [dict(r) for r in cur.fetchall()]


def j(value) -> str:
    return json.dumps(value, ensure_ascii=False)
