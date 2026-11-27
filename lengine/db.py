"""SQLite storage. One file holds every book, the knowledge map and the learner's history."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

SCHEMA = """
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


def connect(path: str | Path = "data/engine.db") -> sqlite3.Connection:
    Path(path).parent.mkdir(parents=True, exist_ok=True) if str(path) != ":memory:" else None
    con = sqlite3.connect(str(path), check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    con.executescript(SCHEMA)
    return con


def rows(cur) -> list[dict]:
    return [dict(r) for r in cur.fetchall()]


def j(value) -> str:
    return json.dumps(value, ensure_ascii=False)
