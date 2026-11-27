"""Background import pipeline with a scripted model: stages, cost confirmation, per-chapter failure and retry."""
import importlib
from pathlib import Path

from fastapi.testclient import TestClient

from tests.fake_llm import FakeLLM
from tests.test_flow import wait_job

PDF = Path(__file__).parent.parent / "fixtures" / "econ-ch2-4.pdf"


class FlakyLLM(FakeLLM):
    """Fails every knowledge call for chapters whose text mentions 'Demand' until healed."""
    def __init__(self):
        super().__init__()
        self.broken = True

    def complete(self, system, user, purpose="chat", json_mode=False):
        if purpose == "knowledge" and self.broken and "Demand" in user[:400]:
            raise RuntimeError("simulated overload")
        return super().complete(system, user, purpose, json_mode)


def _app(tmp_path, monkeypatch, llm):
    monkeypatch.setenv("ENGINE_DATA", str(tmp_path))
    import lengine.api as api
    import lengine.pipeline as pipeline
    importlib.reload(api)
    monkeypatch.setattr(pipeline, "RETRY_DELAY", 0)
    api._llm = llm
    return api, TestClient(api.app)


def test_upload_waits_for_cost_confirmation(tmp_path, monkeypatch):
    llm = FakeLLM()
    api, c = _app(tmp_path, monkeypatch, llm)
    with PDF.open("rb") as f:
        up = c.post("/api/books", files={"file": ("econ.pdf", f, "application/pdf")}).json()
    j = wait_job(c, up["job_id"])
    assert j["status"] == "waiting" and j["estimate_usd"] > 0
    st = {s["stage"]: s["status"] for s in j["stages"]}
    assert [st[k] for k in ("upload", "validate", "extract", "structure", "segment", "chunk")] == ["done"] * 6
    assert st["knowledge"] == "waiting" and "knowledge" not in llm.calls  # nothing paid before the OK
    assert c.get("/api/jobs?active=1").json()[0]["id"] == up["job_id"]

    c.post(f"/api/jobs/{up['job_id']}/confirm")
    j = wait_job(c, up["job_id"], until=("done", "partial", "failed"))
    assert j["status"] == "done", j["error"]
    assert j["chapters_done"] == j["chapters_total"] > 0 and j["progress"] == 1.0
    st = {s["stage"]: s["status"] for s in j["stages"]}
    assert st["embed"] == "skipped" and st["complete"] == "done"
    assert "issues" in j["report"] and j["concepts"] > 0
    assert c.get("/api/jobs?active=1").json() == []


def test_failed_chapter_is_retried_alone(tmp_path, monkeypatch):
    llm = FlakyLLM()
    api, c = _app(tmp_path, monkeypatch, llm)
    with PDF.open("rb") as f:
        up = c.post("/api/books?auto_map=true", files={"file": ("econ.pdf", f, "application/pdf")}).json()
    j = wait_job(c, up["job_id"], until=("done", "partial", "failed"))
    assert j["status"] == "partial" and len(j["chapters_failed"]) == 1
    assert any(i["level"] == "error" for i in j["report"]["issues"])
    done_before = j["chapters_done"]
    calls_before = llm.calls.count("knowledge")

    llm.broken = False
    c.post(f"/api/jobs/{up['job_id']}/retry")
    j = wait_job(c, up["job_id"], until=("done", "failed"))
    assert j["status"] == "done" and j["chapters_failed"] == []
    assert j["chapters_done"] == done_before + 1
    assert llm.calls.count("knowledge") == calls_before + 1  # finished chapters were not redone


def test_duplicate_upload_returns_existing_job(tmp_path, monkeypatch):
    api, c = _app(tmp_path, monkeypatch, FakeLLM())
    with PDF.open("rb") as f:
        a = c.post("/api/books", files={"file": ("econ.pdf", f, "application/pdf")}).json()
    with PDF.open("rb") as f:
        b = c.post("/api/books", files={"file": ("econ.pdf", f, "application/pdf")}).json()
    assert b == {"book_id": a["book_id"], "job_id": a["job_id"], "duplicate": True}


def test_restart_resumes_interrupted_job(tmp_path, monkeypatch):
    api, c = _app(tmp_path, monkeypatch, FakeLLM())
    with PDF.open("rb") as f:
        up = c.post("/api/books", files={"file": ("econ.pdf", f, "application/pdf")}).json()
    wait_job(c, up["job_id"])
    # simulate a crash in the middle of the model stage
    api.con.execute("UPDATE jobs SET status='running', options='{\"map_confirmed\": true}' WHERE id=?", (up["job_id"],))
    api.con.execute("UPDATE job_stages SET status='running' WHERE job_id=? AND stage='knowledge'", (up["job_id"],))
    api.con.commit()
    from lengine import pipeline
    r = pipeline.Runner(api.con, lambda: api._llm)
    r.start()
    j = wait_job(c, up["job_id"], until=("done", "partial", "failed"))
    assert j["status"] == "done"
