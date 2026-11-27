# AI Learning Engine (MVP)

A thin, data-owning learning core with pluggable teaching tools, built after studying
OpenMAIC, DeepTutor, VisualLearning.Skill, Math-To-Manim, StoryCraft and OpenLingo.

Flow: PDF -> chapters + page-tagged chunks -> knowledge map (cited pages, prerequisites) -> learning path
-> AI teacher loop (explain -> check -> apply -> feedback) -> visual tools chosen by knowledge type
-> practice at recall/understand/apply levels -> mastery + error types -> FSRS review -> dashboard.

## Run
    pip install -r requirements.txt
    export ANTHROPIC_API_KEY=...      # Claude API key (platform.claude.com); CLAUDE_API_KEY also works
    # defaults: claude-sonnet-5-5 for the knowledge map, claude-haiku-5-5 for everything else.
    # override any step, e.g. LLM_MODEL_KNOWLEDGE=claude-haiku-5-5 for the cheapest setup
    # or use an OpenAI-compatible provider instead: LLM_PROVIDER=openai LLM_API_KEY=... LLM_BASE_URL=... LLM_MODEL=...
    uvicorn lengine.api:app --port 8000   # open http://localhost:8000

## Windows
Double-click `start_windows.bat` (needs Python 3.11+ from python.org). Put `CLAUDE_API_KEY=...` in a `.env` file
next to it. Your data lives in `data\engine.db`; to move your learning history to another computer, copy the
`data` folder.

## Modules
| file | role | borrowed from |
|---|---|---|
| ingest.py | PDF -> chapters/chunks, FTS5 search with pages | DeepTutor parsing ideas; pypdf (not AGPL PyMuPDF) |
| knowledge.py | chapter knowledge map, validated stage output | Math-To-Manim staged chain, DeepTutor KnowledgeType |
| modes.py | teaching-mode selector (kind x form x domain) | OpenMAIC widget table + DeepTutor knowledge types |
| teacher.py | guided lesson loop, resumable sessions | DeepTutor mastery_path, OpenMAIC teacher |
| practice.py / similarity.py | questions, grading, error types | OpenLingo exercise types + similarity |
| mastery.py | mastery with confidence cap + application gate | DeepTutor mastery.py (Apache-2.0) |
| review.py | FSRS cards per concept, retention by kind | py-fsrs; DeepTutor per-type intervals |
| visuals.py | interactive HTML (browser-checked), Manim | VisualLearning.Skill rules, Math-To-Manim |
| dashboard.py, static/index.html | progress, weak spots, due, mistakes, usage | - |

Tests: `pytest -q` (the end-to-end test uses a scripted fake model to check plumbing only).

## Chinese books
Chinese PDFs work: line breaks that pypdf inserts inside CJK text are rejoined, search uses SQLite's
FTS5 trigram tokenizer (queries are split into 3-character grams), and chapter starts from a faulty
PDF outline are corrected by finding the "第N章" heading. Use `domain="psychology"` for experiment-driven
books such as Kahneman's (prefers case studies and interactive demos).

Your books and the `data/` folder are git-ignored: keep copyrighted material out of the repo.

## Test fixture
`fixtures/econ-ch2-4.pdf` is from OpenStax *Principles of Economics 3e* (Rice University),
licensed CC BY-NC-SA 4.0: https://openstax.org/details/books/principles-economics-3e

## Real run and cost (measured)
`PYTHONPATH=. python scripts/run_chapter.py <book.pdf> --chapter 1 --domain psychology --out data/run.md` runs the
whole flow through the API with a scripted learner: map -> lesson (2 learner turns) -> 6 questions graded -> one
interactive visual -> dashboard, and prints the cost from `llm_usage`.

Measured 2026-11-27 on chapter 1 of a 345-page Chinese book (9 pages, 10 concepts), Claude API list prices:

| step | model | calls | input tok | output tok | USD |
|---|---|---|---|---|---|
| knowledge map | claude-sonnet-5-5 | 1 | 9,363 | 2,765 | 0.0464 |
| lesson (start + 2 replies) | claude-haiku-5-5 | 3 | 8,158 | 2,528 | 0.0021 |
| question generation (6) | claude-haiku-5-5 | 1 | 2,187 | 1,640 | 0.0010 |
| grading (3 open answers) | claude-haiku-5-5 | 3 | 1,481 | 1,045 | 0.0007 |
| interactive visual | claude-haiku-5-5 | 1 | 2,256 | 4,918 | 0.0027 |
| **total** | | 9 | 23,445 | 12,896 | **0.0528** |

Wall time 87 s. Extrapolated: one chapter fully studied (map + every concept taught, quizzed and visualised)
is about $0.11, so about $4 for all 38 chapters of this book; without a visual per concept about $3.
