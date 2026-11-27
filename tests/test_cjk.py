import sqlite3

from lengine import db, ingest
from lengine.modes import choose_tools


def test_normalize_rejoins_cjk_lines():
    assert ingest.normalize_text("2001\n年\n12\n月，以\n色列") == "2001年12月，以色列"
    assert ingest.normalize_text("plain\nenglish") == "plain\nenglish"


def test_fix_starts_moves_chapter_to_its_heading():
    pages = ["第\n30\n章\n 罕见事件", "正文", "第\n31\n章\n 风险政策", "正文"]
    marks = [("第30章 罕见事件", 1), ("第31章 风险政策", 1)]
    assert ingest._fix_starts(marks, pages) == [("第30章 罕见事件", 1), ("第31章 风险政策", 3)]


def test_cjk_search_hits_trigram_index():
    con = db.connect(":memory:")
    bid = con.execute("INSERT INTO books(title, source_path, domain) VALUES ('t','t','psychology')").lastrowid
    ch = con.execute("INSERT INTO chapters(book_id, ord, title, page_start, page_end) VALUES (?,0,'c',1,1)", (bid,)).lastrowid
    for i, text in enumerate(["锚定效应在生活中随处可见", "可得性启发法"]):
        cid = con.execute("INSERT INTO chunks(book_id, chapter_id, page, ord, text) VALUES (?,?,?,?,?)",
                          (bid, ch, i + 1, i, text)).lastrowid
        con.execute("INSERT INTO chunks_fts(rowid, text) VALUES (?,?)", (cid, text))
    hits = ingest.search(con, bid, "什么是锚定效应")
    assert hits and hits[0]["page"] == 1


def test_psychology_pack_prefers_case_study():
    assert choose_tools("concept", "case", domain="psychology")[0] == "case_study"
