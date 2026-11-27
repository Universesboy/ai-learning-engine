"""Real end-to-end run on one chapter through the HTTP API, with a scripted learner.

Usage: python scripts/run_chapter.py data/book.pdf --chapter 1 --domain psychology --out data/run.md
Writes a transcript (Markdown) and prints the cost table from llm_usage. Needs a Claude API key.
"""
from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

ap = argparse.ArgumentParser()
ap.add_argument("pdf")
ap.add_argument("--chapter", type=int, default=1)
ap.add_argument("--domain", default="general")
ap.add_argument("--title")
ap.add_argument("--out", default="data/run.md")
ap.add_argument("--data", default="data/run")
args = ap.parse_args()
os.environ["ENGINE_DATA"] = args.data

from fastapi.testclient import TestClient  # noqa: E402
import lengine.api as api  # noqa: E402
from lengine.llm import cost_usd  # noqa: E402

c = TestClient(api.app)
log: list[str] = []
t0 = time.time()


def say(s: str = ""):
    print(s, flush=True)
    log.append(s)


def ok(r):
    if r.status_code >= 400:
        raise SystemExit(f"{r.request.url}: {r.status_code} {r.text[:500]}")
    return r.json()


with open(args.pdf, "rb") as f:
    book = ok(c.post(f"/api/books?domain={args.domain}" + (f"&title={args.title}" if args.title else ""),
                     files={"file": (Path(args.pdf).name, f, "application/pdf")}))["book_id"]
b = ok(c.get(f"/api/books/{book}"))
ch = b["chapters"][args.chapter - 1]
say(f"# Run: {ch['title']} (pp.{ch.get('page_start')}-{ch.get('page_end')})\n")

concepts = ok(c.post(f"/api/chapters/{ch['id']}/map"))["concepts"]
say(f"## Knowledge map: {len(concepts)} concepts\n")
for x in concepts:
    say(f"- **{x['name']}** ({x['kind']}/{x['form']}, p.{','.join(map(str, x['pages']))})"
        f" requires {x['requires'] or '-'}: {x.get('summary','')}")
path = [p for p in ok(c.get(f"/api/books/{book}"))["path"] if p["chapter_id"] == ch["id"]]
say("\n**Learning path:** " + " → ".join(p["name"] for p in path) + "\n")

first = path[0]
lesson = ok(c.post(f"/api/concepts/{first['id']}/lesson"))
say(f"## Lesson: {first['name']}\n\nTools chosen: {lesson.get('tools')}\n\n**Teacher:** {lesson.get('explanation')}\n\n**Check:** {lesson.get('check_question')}\n")
learner = ["我理解的是：系统1是自动、快速、不费力的；系统2需要集中注意力，比如心算17×24。",
           "如果我很累的时候还要做复杂决定，系统2会偷懒，我可能直接接受系统1的第一个直觉答案。"]
for msg in learner:
    r = ok(c.post(f"/api/sessions/{lesson['session_id']}/reply", json={"message": msg}))
    say(f"**Learner:** {msg}\n\n**Teacher ({r.get('step')}, understood={r.get('understood')}):** {r.get('reply')}\n\n**Next:** {r.get('next_question')}\n")
    if r.get("step") == "done":
        break

qs = ok(c.post(f"/api/concepts/{first['id']}/questions"))
say(f"## Practice: {len(qs)} questions (learner answers every other one wrong on purpose)\n")
for i, q in enumerate(qs):
    row = api.con.execute("SELECT answer FROM questions WHERE id=?", (q["id"],)).fetchone()
    right = json.loads(row["answer"]) if row["answer"] and row["answer"].startswith(('"', '[', '{')) else row["answer"]
    if q["qtype"] in ("single_choice", "true_false", "fill_in"):
        resp = str(right) if i % 2 == 0 else {"single_choice": "D", "true_false": "false" if str(right).lower() == "true" else "true"}.get(q["qtype"], "不知道")
    else:
        resp = "系统1会先给出直觉答案，系统2如果没有被调动就会直接认可它。" if i % 2 == 0 else "这是系统2的作用，因为需要思考。"
    g = ok(c.post(f"/api/questions/{q['id']}/answer", json={"response": resp}))
    say(f"- [{q['qtype']}/{q.get('level')}] {q.get('prompt') or q.get('question')}\n  - answer: {resp} → score {g.get('score')}, error: {g.get('error_type')}, feedback: {g.get('feedback','')}")

v = ok(c.post(f"/api/concepts/{first['id']}/visual"))
say(f"\n## Visual: artifact {v.get('artifact_id')}, browser check problems: {v.get('problems')}\n")

home = ok(c.get("/api/home"))
say(f"## Dashboard\n\n```json\n{json.dumps(home, ensure_ascii=False, indent=1)[:3000]}\n```\n")

say("## Cost (actual usage)\n\n| step | model | calls | input tok | output tok | USD | seconds |\n|---|---|---|---|---|---|---|")
total = 0.0
for r in api.con.execute("SELECT purpose, model, COUNT(*) n, SUM(prompt_tokens) p, SUM(completion_tokens) o, SUM(seconds) s "
                         "FROM llm_usage GROUP BY purpose, model ORDER BY MIN(id)"):
    usd = sum(cost_usd(x["model"], x["prompt_tokens"], x["completion_tokens"]) or 0 for x in api.con.execute(
        "SELECT * FROM llm_usage WHERE purpose=? AND model=?", (r["purpose"], r["model"])))
    total += usd
    say(f"| {r['purpose']} | {r['model']} | {r['n']} | {r['p']} | {r['o']} | {usd:.4f} | {r['s']:.0f} |")
say(f"\n**Total: ${total:.4f}**, wall time {time.time()-t0:.0f}s")
Path(args.out).write_text("\n".join(log), encoding="utf-8")
