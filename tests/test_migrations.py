"""2.0 migrations: an old database upgrades in place, keeps every row, and is backed up first."""
import sqlite3
from pathlib import Path

from lengine import db, knowledge
from tests.fake_llm import FakeLLM

OLD = db.BASE_SCHEMA  # the pre-2.0 schema (UNIQUE(book_id, key))


def make_old(path: Path):
    con = sqlite3.connect(path)
    con.executescript(OLD)
    con.execute("INSERT INTO books(id,title) VALUES (1,'b')")
    con.execute("INSERT INTO chapters(id,book_id,ord,title,page_start,page_end) VALUES (1,1,0,'c1',24,26),(2,1,1,'c2',27,30)")
    con.execute("INSERT INTO concepts(id,book_id,chapter_id,key,name,kind,form,pages) VALUES "
                "(1,1,1,'demand','需求','concept','parametric','[24]'),(2,1,2,'other','其他','concept','definition','[27]')")
    # the collision bug's footprint: a prerequisite that crosses chapters
    con.execute("INSERT INTO prerequisites VALUES (2,1)")
    con.execute("INSERT INTO questions(id,concept_id,qtype,level,prompt) VALUES (1,1,'short_answer','recall','q')")
    con.execute("INSERT INTO attempts(question_id,concept_id,score) VALUES (1,1,0.9)")
    con.commit()
    con.close()


def test_upgrade_keeps_rows_and_backs_up(tmp_path):
    p = tmp_path / "engine.db"
    make_old(p)
    con = db.connect(p)
    assert con.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION
    assert con.execute("SELECT count(*) FROM concepts").fetchone()[0] == 2
    assert con.execute("SELECT count(*) FROM attempts").fetchone()[0] == 1
    assert list(tmp_path.glob("engine.db.bak-v0-*"))
    # the same key may now exist in two chapters
    con.execute("INSERT INTO concepts(book_id,chapter_id,key,name,kind,form) VALUES (1,2,'demand','需求2','concept','definition')")
    assert [c["id"] for c in knowledge.chapters_needing_repair(con)] == [1, 2]
    con.close()
    db.connect(p).close()  # re-opening does not migrate or back up again
    assert len(list(tmp_path.glob("engine.db.bak-*"))) == 1


def test_rebuild_keeps_concepts_with_history(tmp_path):
    p = tmp_path / "engine.db"
    make_old(p)
    con = db.connect(p)
    con.execute("UPDATE chapters SET page_start=24, page_end=28 WHERE id=1")
    con.execute("INSERT INTO chunks(book_id,chapter_id,page,ord,text) VALUES (1,1,24,0,'demand text')")
    knowledge.build_chapter_map(con, FakeLLM(), 1)
    names = {r["name"]: r["id"] for r in con.execute("SELECT id, name FROM concepts WHERE chapter_id=1")}
    assert names["需求"] == 1  # same name: the concept with an attempt keeps its id and history
    assert {"供给", "均衡"} <= set(names)
    assert knowledge.chapters_needing_repair(con) == []  # the crossing prerequisite is gone
