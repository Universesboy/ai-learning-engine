from datetime import datetime, timedelta, timezone
from lengine import db, mastery, modes, review

def test_mastery_needs_application():
    assert mastery.compute_mastery([1.0]) == 0.5
    s = mastery.concept_status({"recall": [1, 1, 1], "understand": [1, 1]})
    assert s["status"] == "cannot_apply_yet"
    s = mastery.concept_status({"recall": [1, 1], "understand": [1], "apply": [0.9, 1]})
    assert s["status"] == "mastered"
    assert mastery.concept_status({"recall": [0, 0.2]})["status"] == "needs_relearning"

def test_review_schedules_by_score():
    con = db.connect(":memory:")
    con.execute("INSERT INTO books(id,title) VALUES (1,'b')")
    con.execute("INSERT INTO concepts(id,book_id,key,name,kind,form) VALUES (1,1,'a','A','concept','parametric'),(2,1,'b','B','concept','parametric')")
    t0 = datetime(2026, 11, 27, tzinfo=timezone.utc)
    good = review.review(con, 1, "concept", 1.0, t0)
    bad = review.review(con, 2, "concept", 0.1, t0)
    assert good["due"] > bad["due"]
    assert [d["concept_id"] for d in review.due_cards(con, 1, t0 + timedelta(hours=1))] == [2]

def test_mode_selection():
    assert modes.choose_tools("concept", "parametric", "economics")[0] == "interactive_sim"
    assert modes.choose_tools("concept", "derivation", "math_physics")[0] == "manim"
    assert "flashcards" in modes.choose_tools("memory", "definition")
    assert modes.choose_tools("concept", "parametric", mastery=0.9)[0] == "case_study"
