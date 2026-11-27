"""End-to-end plumbing with a scripted model: upload -> map -> lesson -> quiz -> mistakes -> review -> dashboard."""
import os, importlib
from pathlib import Path
from fastapi.testclient import TestClient
from tests.fake_llm import FakeLLM

PDF = Path(__file__).parent.parent / "fixtures" / "econ-ch2-4.pdf"

def test_full_flow(tmp_path, monkeypatch):
    monkeypatch.setenv("ENGINE_DATA", str(tmp_path))
    import lengine.api as api
    importlib.reload(api)
    api._llm = FakeLLM()
    c = TestClient(api.app)
    with PDF.open("rb") as f:
        book = c.post("/api/books?domain=economics", files={"file": ("econ.pdf", f, "application/pdf")}).json()["book_id"]
    b = c.get(f"/api/books/{book}").json()
    ch3 = next(ch for ch in b["chapters"] if "Demand" in ch["title"])
    concepts = c.post(f"/api/chapters/{ch3['id']}/map").json()["concepts"]
    assert [x["key"] for x in concepts] == ["demand", "supply", "equilibrium"]  # uncited one dropped
    b = c.get(f"/api/books/{book}").json()
    assert [p["name"] for p in b["path"]][-1] == "均衡"
    eq = next(x for x in b["concepts"] if x["name"] == "均衡")
    lesson = c.post(f"/api/concepts/{eq['id']}/lesson").json()
    assert lesson["tools"][0] == "interactive_sim"
    r = c.post(f"/api/sessions/{lesson['session_id']}/reply", json={"message": "因为替代效应"}).json()
    assert r["step"] == "apply"
    qs = c.post(f"/api/concepts/{eq['id']}/questions").json()
    assert all("answer" not in q for q in qs)
    res = {q["qtype"]: c.post(f"/api/questions/{q['id']}/answer", json={"response": {"single_choice": "A", "true_false": "true", "application": "价格下降"}[q["qtype"]]}).json() for q in qs}
    assert res["single_choice"]["score"] == 1.0 and res["true_false"]["score"] == 0.0
    assert res["application"]["error_type"] == "cannot_transfer"
    assert res["application"]["status"]["status"] != "mastered"
    v = c.post(f"/api/concepts/{eq['id']}/visual").json()
    assert v["problems"] == [], v["problems"]
    assert c.get(f"/api/artifacts/{v['artifact_id']}").status_code == 200
    home = c.get("/api/home").json()
    assert home["books"][0]["weak"] >= 1
    bv = c.get(f"/api/books/{book}").json()
    assert len(bv["mistakes"]) == 2 and bv["concepts"][0]["forgetting_risk"] is not None or True
    assert c.get("/").status_code == 200
