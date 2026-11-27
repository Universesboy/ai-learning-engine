"""Memory & Review Engine: one FSRS card per concept (py-fsrs, MIT).

FSRS replaces OpenLingo's SM-2: the open benchmark shows FSRS-6 predicting recall better than SM-2
for 99.6% of users. Like DeepTutor, retention targets differ by knowledge type: facts to memorise
get a higher target (shorter intervals) than design/analysis skills, which are re-tested by
application tasks instead.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from fsrs import Card, Rating, Scheduler

RETENTION_BY_KIND = {"memory": 0.92, "concept": 0.9, "procedure": 0.88, "design": 0.85}
_schedulers = {k: Scheduler(desired_retention=v, enable_fuzzing=False) for k, v in RETENTION_BY_KIND.items()}


def rating_from_score(score: float) -> Rating:
    if score < 0.4:
        return Rating.Again
    if score < 0.7:
        return Rating.Hard
    if score < 0.95:
        return Rating.Good
    return Rating.Easy


def review(con: sqlite3.Connection, concept_id: int, kind: str, score: float,
           when: datetime | None = None) -> dict:
    when = when or datetime.now(timezone.utc)
    row = con.execute("SELECT state FROM cards WHERE concept_id=?", (concept_id,)).fetchone()
    card = Card.from_json(row["state"]) if row else Card()
    sched = _schedulers.get(kind, _schedulers["concept"])
    card, _ = sched.review_card(card, rating_from_score(score), review_datetime=when)
    con.execute("INSERT INTO cards(concept_id, state, due) VALUES (?,?,?) "
                "ON CONFLICT(concept_id) DO UPDATE SET state=excluded.state, due=excluded.due",
                (concept_id, card.to_json(), card.due.isoformat()))
    con.commit()
    return {"due": card.due.isoformat(), "stability": card.stability,
            "retrievability": sched.get_card_retrievability(card, when)}


def due_cards(con: sqlite3.Connection, book_id: int | None = None, now: datetime | None = None) -> list[dict]:
    now = now or datetime.now(timezone.utc)
    sql = ("SELECT k.concept_id, k.due, c.name, c.kind, c.book_id, k.state FROM cards k "
           "JOIN concepts c ON c.id = k.concept_id WHERE k.due <= ?")
    args: list = [now.isoformat()]
    if book_id:
        sql += " AND c.book_id = ?"
        args.append(book_id)
    out = []
    for r in con.execute(sql + " ORDER BY k.due", args):
        d = dict(r)
        card = Card.from_json(d.pop("state"))
        d["retrievability"] = round(_schedulers.get(d["kind"], _schedulers["concept"])
                                    .get_card_retrievability(card, now), 3)
        out.append(d)
    return out


def forgetting_risk(con: sqlite3.Connection, concept_id: int, kind: str, now: datetime | None = None) -> float | None:
    row = con.execute("SELECT state FROM cards WHERE concept_id=?", (concept_id,)).fetchone()
    if not row:
        return None
    now = now or datetime.now(timezone.utc)
    return round(1 - _schedulers.get(kind, _schedulers["concept"]).get_card_retrievability(Card.from_json(row["state"]), now), 3)
