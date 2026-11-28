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


# ---- structure rules found on real books (2026-11-28): talks, no outline, stray headings, long chapters ----
def _page(head, n=900, ch="知"):
    return head + "\n" + ch * n


def test_talks_after_the_last_chapter_are_body_not_conclusion():
    assert ingest._classify("Talk Eleven The Psychology of Human Misjudgment", False) == "body"
    assert ingest._classify("结语", False) == "conclusion"
    assert ingest._classify("附录A 不确定性", False) == "appendix"
    assert ingest._classify("Foreword by Warren E. Buffett", True) == "front"


def test_no_outline_uses_chinese_headings_and_skips_contents_page():
    toc = "目录\n第一章 起点 3\n第二章 发展 5\n第三章 结局 7"
    pages = [_page("封面", 10), toc] + [_page("第一章\n起点")] + [_page("")] + [_page("第二章 发展")] + [_page("")] + \
            [_page("第三章：结局")] + [_page("")] * 40
    chs = ingest.detect_structure(None, pages)
    body = [c for c in chs if c.kind == "body"]
    assert [c.title for c in body] == ["第一章 起点", "第二章 发展", "第三章 结局"]
    assert body[0].page_start == 3 and chs[0].page_start == 1 and chs[0].kind == "skip"  # cover + contents kept, not mapped


def test_stray_heading_in_running_text_falls_back_to_page_windows():
    pages = [_page("some text", 3000, "a ")] * 60
    pages[49] = "Chapter 19.\nIn the Epilogue I explore\n" + pages[49]
    chs = ingest.detect_structure(None, pages)
    assert chs[0].page_start == 1 and chs[-1].page_end == 60 and len(chs) > 3
    assert all(b.page_start == a.page_end + 1 for a, b in zip(chs, chs[1:]))  # every page covered once


def test_long_chapter_is_split_without_losing_pages():
    pages = [_page("第一章 很长", 2000)] + [_page("", 2000)] * 59 + [_page("第二章 短")] + [_page("第三章 短")]
    chs = ingest.detect_structure(None, pages)
    long_parts = [c for c in chs if c.title.startswith("第一章")]
    assert len(long_parts) >= 3 and long_parts[0].title.endswith(f"（1/{len(long_parts)}）")
    assert long_parts[0].page_start == 1 and long_parts[-1].page_end == 60
    assert all(sum(ingest._text_tokens(pages[p - 1]) for p in range(c.page_start, c.page_end + 1)) <= ingest.MAX_SEGMENT_TOKENS + 2100
               for c in long_parts)


def test_titles_lose_soft_hyphens_and_nuls():
    assert ingest.clean_title("Psy\xadchology of\nHuman\x00 Misjudgment ") == "Psychology of Human Misjudgment"


def test_watermark_lines_repeated_on_most_pages_are_dropped():
    from lengine.ingest import strip_repeated
    pages = [f"股窜网\nwww.gucuan.com\n第{i}页的正文内容，各不相同。" for i in range(10)]
    pages[3] = "只有正文，没有水印"
    out = strip_repeated(pages)
    assert all("gucuan" not in t and "股窜网" not in t for t in out)
    assert out[0] == "第0页的正文内容，各不相同。" and out[3] == "只有正文，没有水印"
    short = ["水印水印\n正文"] * 3  # too few pages to call anything a watermark
    assert strip_repeated(short) == short
