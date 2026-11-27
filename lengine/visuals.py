"""Visual Learning Studio.

interactive_sim / walkthrough: a self-contained HTML page written by the model under rules condensed
from VisualLearning.Skill (MIT, andrewalumkal): accuracy first, label simplifications, a Sources box
citing textbook pages, fundamentals first, every control explains itself. Then a headless browser
checks the page actually runs (no JS errors, has interactive controls) before it is shown; one repair
round on failure.

manim: Math-To-Manim-style chain shrunk to teaching size: design (beats, formulas) -> code -> static
check -> render -> one repair round.
"""
from __future__ import annotations

import ast
import json
import os
import re
import sqlite3
import subprocess
import tempfile
from pathlib import Path

from . import ingest
from .db import j
from .llm import LLM, complete_json

HTML_SYSTEM = """You build ONE self-contained interactive HTML page that teaches a single concept from a textbook.
Rules (from the VisualLearning skill):
- Accuracy comes first: every mechanism, number and label must match the excerpts. Never invent data;
  if you use illustrative numbers, say so on the page.
- Fundamentals first: the first screen shows the core idea; detail is optional.
- Interactive: sliders/buttons let the learner change inputs and SEE the consequence (e.g. shift a curve, watch the equilibrium move). Motion only to show a mechanism.
- Every control and graph element has a short plain-language label or tooltip.
- A visible "简化说明" box lists simplifications; a "来源" box cites textbook pages (p.N).
- One or two "试一试" prompts that ask the learner to predict before moving a slider.
- Flat 2D, calm colours, no external network requests (no CDNs); plain JS + inline SVG or canvas.
- Must work at 360px width.
Return JSON {"title": "...", "html": "<!doctype html>...", "claims": ["each factual claim the page makes, with (p.N)"]}
Write visible text in {lang}."""

MANIM_DESIGN = """Design a 30-60 second Manim teaching animation for ONE concept, grounded in the excerpts.
Return JSON {"beats":[{"t":"what appears/moves","why":"what it teaches"}], "formulas":["LaTeX"], "objects":["axes","curve",...]}"""

MANIM_CODE = """Write Manim Community Edition (v0.19+) Python for this design. One Scene subclass named TeachScene.
Use Axes/MathTex/Text/ValueTracker; keep labels short; no external files; total run time <= 60s.
Return JSON {"code": "from manim import *\\n..."}"""


def _excerpts(con, c) -> str:
    hits = ingest.search(con, c["book_id"], f"{c['name']} {c['summary']}", k=6, chapter_id=c["chapter_id"])
    return "\n".join(f"[p.{h['page']}] {h['text']}" for h in hits)


def check_html(path: str) -> list[str]:
    """Load the page headless; report JS errors and missing interactivity."""
    script = r"""
const { chromium } = require('playwright');
(async () => { const b = await chromium.launch(); const p = await b.newPage({viewport:{width:1000,height:800}});
 const errs = []; p.on('pageerror', e => errs.push('pageerror: ' + e.message));
 p.on('console', m => { if (m.type() === 'error') errs.push('console: ' + m.text()); });
 p.on('request', r => { if (!r.url().startsWith('file:') && !r.url().startsWith('data:')) errs.push('network: ' + r.url()); });
 await p.goto('file://' + process.argv[2]); await p.waitForTimeout(800);
 const n = await p.locator('input,button,select').count(); if (n === 0) errs.push('no interactive controls');
 const sl = p.locator('input[type=range]').first();
 if (await sl.count()) { await sl.focus(); for (let i=0;i<5;i++) await p.keyboard.press('ArrowRight'); await p.waitForTimeout(300); }
 await p.screenshot({ path: process.argv[2].replace(/\.html$/, '.png') });
 console.log(JSON.stringify(errs)); await b.close(); })();"""
    import shutil
    node, npm = shutil.which("node"), shutil.which("npm")
    if not node or not npm:  # no Node/Playwright on this machine (e.g. a plain Windows install): skip the check
        return []
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
        f.write(script)
    try:
        npm_root = subprocess.run([npm, "root", "-g"], capture_output=True, text=True).stdout.strip()
        out = subprocess.run([node, f.name, str(Path(path).resolve())], capture_output=True, text=True,
                             timeout=120, env={**os.environ, "NODE_PATH": npm_root})
    except Exception as e:
        return [f"checker failed: {e}"]
    if "Cannot find module 'playwright'" in out.stderr:
        return []
    try:
        return json.loads(out.stdout.strip().splitlines()[-1])
    except Exception:
        return [f"checker failed: {out.stderr[-300:]}"]


def interactive_page(con: sqlite3.Connection, llm: LLM, concept_id: int, out_dir: str = "data/artifacts",
                     lang: str = "Chinese") -> dict:
    c = dict(con.execute("SELECT * FROM concepts WHERE id=?", (concept_id,)).fetchone())
    user = f"Concept: {c['name']}\nSummary: {c['summary']}\nFormulas: {c['formulas']}\nExcerpts:\n{_excerpts(con, c)}"
    system = HTML_SYSTEM.replace("{lang}", lang)
    data = complete_json(llm, system, user, purpose="visual", book_id=c["book_id"])
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    path = str(Path(out_dir) / f"concept-{concept_id}-sim.html")
    problems: list[str] = []
    for attempt in range(2):
        Path(path).write_text(data.get("html", ""), encoding="utf-8")
        problems = check_html(path)
        if not problems:
            break
        data = complete_json(llm, system, user + f"\n\nYour previous page failed these checks, fix them:\n{problems}\n"
                             f"Previous HTML:\n{data.get('html','')[:20000]}", purpose="visual", book_id=c["book_id"])
    aid = con.execute("INSERT INTO artifacts(concept_id, kind, path, meta) VALUES (?,?,?,?)",
                      (concept_id, "interactive_sim", path,
                       j({"title": data.get("title"), "claims": data.get("claims", []), "problems": problems}))).lastrowid
    con.commit()
    return {"artifact_id": aid, "path": path, "problems": problems, "claims": data.get("claims", [])}


def static_check_manim(code: str) -> list[str]:
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return [f"syntax error line {e.lineno}: {e.msg}"]
    names = [n.name for n in tree.body if isinstance(n, ast.ClassDef)]
    probs = [] if "TeachScene" in names else ["no class TeachScene"]
    # Math-To-Manim found unbraced \tfrac12-style fragments break LaTeX; catch the common case.
    if re.search(r"\\[dt]?frac\d", code):
        probs.append(r"unbraced \frac argument")
    return probs


def manim_animation(con: sqlite3.Connection, llm: LLM, concept_id: int, out_dir: str = "data/artifacts",
                    quality: str = "l") -> dict:
    c = dict(con.execute("SELECT * FROM concepts WHERE id=?", (concept_id,)).fetchone())
    ctx = f"Concept: {c['name']}\nSummary: {c['summary']}\nFormulas: {c['formulas']}\nExcerpts:\n{_excerpts(con, c)}"
    design = complete_json(llm, MANIM_DESIGN, ctx, purpose="visual", book_id=c["book_id"])
    if len(design.get("beats", [])) < 3:
        raise ValueError("animation design too thin")
    code = complete_json(llm, MANIM_CODE, json.dumps(design, ensure_ascii=False), purpose="visual", book_id=c["book_id"]).get("code", "")
    work = Path(out_dir) / f"concept-{concept_id}-manim"
    work.mkdir(parents=True, exist_ok=True)
    err = ""
    for attempt in range(2):
        probs = static_check_manim(code)
        if not probs:
            (work / "scene.py").write_text(code)
            r = subprocess.run(["manim", f"-q{quality}", "--media_dir", str(work / "media"), "scene.py", "TeachScene"],
                               cwd=work, capture_output=True, text=True, timeout=600)
            if r.returncode == 0:
                mp4 = next((work / "media").rglob("TeachScene.mp4"), None)
                aid = con.execute("INSERT INTO artifacts(concept_id, kind, path, meta) VALUES (?,?,?,?)",
                                  (concept_id, "manim", str(mp4), j({"design": design}))).lastrowid
                con.commit()
                return {"artifact_id": aid, "path": str(mp4)}
            err = r.stderr[-2000:]
        else:
            err = "; ".join(probs)
        code = complete_json(llm, MANIM_CODE, json.dumps(design, ensure_ascii=False) +
                             f"\n\nPrevious code failed:\n{err}\n\nCode:\n{code}", purpose="visual", book_id=c["book_id"]).get("code", "")
    raise RuntimeError(f"manim render failed: {err[-500:]}")
