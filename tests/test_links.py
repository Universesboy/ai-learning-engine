"""Phase 4 plumbing with a scripted model and a fake embedder: embeddings, chapter summaries, relations
with program-checked quotes, cross-book answers with checked citations, graph data."""
import importlib
from pathlib import Path

from fastapi.testclient import TestClient

from tests.fake_llm import FakeEmbedder, FakeLLM
from tests.test_flow import wait_job

PDF = Path(__file__).parent.parent / "fixtures" / "econ-ch2-4.pdf"


def _app(tmp_path, monkeypatch):
    monkeypatch.setenv("ENGINE_DATA", str(tmp_path))
    import lengine.api as api
    import lengine.embed as embed
    import lengine.pipeline as pipeline
    importlib.reload(api)
    monkeypatch.setattr(pipeline, "RETRY_DELAY", 0)
    embed.set_embedder(FakeEmbedder())
    api._llm = FakeLLM()
    return api, TestClient(api.app)


def _upload(c, tmp_path, name, extra=b""):
    data = PDF.read_bytes() + extra  # trailing bytes: a different file (new hash), same content
    up = c.post("/api/books?auto_map=true", files={"file": (name, data, "application/pdf")}).json()
    return up, wait_job(c, up["job_id"], until=("done", "partial", "failed"))


def test_import_links_new_book_to_library(tmp_path, monkeypatch):
    api, c = _app(tmp_path, monkeypatch)
    up1, j1 = _upload(c, tmp_path, "econ.pdf")
    assert j1["status"] == "done", j1["error"]
    st = {s["stage"]: s for s in j1["stages"]}
    assert st["embed"]["status"] == "done" and st["graph"]["status"] == "done"
    assert api.con.execute("SELECT count(*) FROM vectors WHERE kind='concept'").fetchone()[0] == j1["concepts"]
    ch = api.con.execute("SELECT summary, core_ideas, arguments, cases FROM chapters WHERE summary IS NOT NULL LIMIT 1").fetchone()
    assert ch["summary"] and "9999" not in ch["arguments"]  # an argument citing a page outside the chapter is dropped

    up2, j2 = _upload(c, tmp_path, "econ-copy.pdf", b"\n%copy\n")
    assert j2["status"] == "done", j2["error"]
    rels = api.con.execute("SELECT r.*, s.book_id sb, t.book_id tb FROM relations r JOIN concepts s ON s.id=r.source_id "
                           "JOIN concepts t ON t.id=r.target_id").fetchall()
    assert any(r["sb"] != r["tb"] for r in rels)  # the second book was linked to the first
    checks = dict(api.con.execute("SELECT result, count(*) FROM relation_checks GROUP BY result").fetchall())
    assert checks.get("unverified", 0) >= 1  # invented quotes never become relations
    for r in rels:  # every stored quote really is in the text of the page it cites
        for cid, quote, page in ((r["source_id"], r["source_quote"], r["source_page"]), (r["target_id"], r["target_quote"], r["target_page"])):
            text = "".join(t for (t,) in api.con.execute(
                "SELECT text FROM chunks WHERE page=? AND chapter_id=(SELECT chapter_id FROM concepts WHERE id=?)", (page, cid)))
            assert quote.replace(" ", "")[:10] in text.replace(" ", "").replace("\n", "")
    # extends is directional: the fake answers B->A, so source/target are swapped relative to the pair
    assert all(r["type"] in ("extends", "similar_to") for r in rels)

    g = c.get("/api/graph").json()
    assert g["relations"] and g["chapters"] and len(g["concepts"]) == j1["concepts"] + j2["concepts"]
    cid = rels[0]["source_id"]
    detail = c.get(f"/api/concepts/{cid}").json()
    assert detail["relations"] and detail["relations"][0]["label"] in ("理论延伸", "相似概念")
    assert c.get("/api/links").json()["stats"]["cross_book"] >= 1


def test_link_job_for_existing_books_waits_for_confirmation(tmp_path, monkeypatch):
    api, c = _app(tmp_path, monkeypatch)
    up1, _ = _upload(c, tmp_path, "econ.pdf")
    up2, _ = _upload(c, tmp_path, "econ-copy.pdf", b"\n%copy\n")
    # simulate pre-Phase-4 books: no summaries, no vectors, no relations
    for t in ("relations", "relation_checks", "vectors"):
        api.con.execute(f"DELETE FROM {t}")
    api.con.execute("UPDATE chapters SET summary=NULL")
    api.con.commit()
    calls = len(api._llm.calls)
    j = c.post("/api/links", json={"book_ids": [up1["book_id"], up2["book_id"]]}).json()
    j = wait_job(c, j["id"], until=("waiting", "failed"))
    assert j["status"] == "waiting" and len(api._llm.calls) == calls  # embeddings only, nothing paid yet
    assert api.con.execute("SELECT count(*) FROM vectors").fetchone()[0] > 0
    c.post(f"/api/jobs/{j['id']}/confirm")
    j = wait_job(c, j["id"], until=("done", "failed"))
    assert j["status"] == "done", j["error"]
    assert api.con.execute("SELECT count(*) FROM relations").fetchone()[0] > 0
    # running it again judges nothing twice
    j2 = c.post("/api/links", json={"book_ids": [up1["book_id"], up2["book_id"]]}).json()
    j2 = wait_job(c, j2["id"], until=("waiting", "failed"))
    assert "0 章要点 + 0 对候选" in next(s["note"] for s in j2["stages"] if s["stage"] == "summaries")


def test_crossbook_answer_checks_every_citation(tmp_path, monkeypatch):
    api, c = _app(tmp_path, monkeypatch)
    _upload(c, tmp_path, "econ.pdf")
    r = c.post("/api/crossbook", json={"question": "需求和价格是什么关系？demand price"}).json()
    assert r["passages"] and all(p["page"] and p["book"] for p in r["passages"])
    view = r["by_book"][0]["views"][0]
    assert view["verified"] and view["cites"]
    assert r["synthesis"][0]["verified"] is False and r["synthesis"][0]["cites"] == []  # quote not in passage: marked
    assert r["stats"] == {"views": 2, "verified": 1}
    hist = c.get("/api/crossbook").json()
    assert hist[0]["question"].startswith("需求")
    assert c.get(f"/api/crossbook/{hist[0]['id']}").json()["stats"]["verified"] == 1
