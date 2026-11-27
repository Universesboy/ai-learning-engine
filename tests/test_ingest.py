from pathlib import Path
from lengine import db, ingest

PDF = str(Path(__file__).parent.parent / "fixtures" / "econ-ch2-4.pdf")

def test_ingest_keeps_chapters_and_pages():
    con = db.connect(":memory:")
    book = ingest.ingest_pdf(con, PDF, "Econ excerpt")
    chs = db.rows(con.execute("SELECT * FROM chapters WHERE book_id=? ORDER BY ord", (book,)))
    titles = [c["title"] for c in chs]
    assert any("Demand and Supply" in t for t in titles)
    ds = next(c for c in chs if "Demand and Supply" in c["title"])
    assert ds["page_start"] < ds["page_end"]
    hits = ingest.search(con, book, "law of demand price quantity demanded", chapter_id=ds["id"])
    assert hits and all(ds["page_start"] <= h["page"] <= ds["page_end"] for h in hits)
