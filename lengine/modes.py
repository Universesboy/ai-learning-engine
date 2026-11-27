"""Teaching-mode selector.

Each concept carries two labels from the knowledge map:
  kind  - what learning it needs (DeepTutor's KnowledgeType): memory / concept / procedure / design
  form  - how it shows up (OpenMAIC's widget table, extended): parametric / process / structure /
          timeline / derivation / code / case / definition
A domain pack (economics, investing, history...) can re-weight the choice; the core stays generic.
"""
from __future__ import annotations

TOOLS = {
    "explain": "AI teacher explanation with textbook citations",
    "interactive_sim": "interactive HTML simulation with sliders (VisualLearning explorer rules)",
    "walkthrough": "narrated step-by-step animated walkthrough (VisualLearning walkthrough)",
    "manim": "Manim animation for derivations and geometry (Math-To-Manim stage chain)",
    "diagram": "process / relationship diagram",
    "timeline": "timeline and relationship map",
    "code_trace": "code execution trace with exercises",
    "case_study": "case analysis and decision simulation",
    "flashcards": "flashcards with spaced repetition",
    "story": "short narrative / story version (StoryCraft-style scenes)",
}

_BY_FORM = {
    "parametric": ["interactive_sim", "explain"],
    "process": ["walkthrough", "diagram"],
    "structure": ["diagram", "explain"],
    "timeline": ["timeline", "story"],
    "derivation": ["manim", "explain"],
    "code": ["code_trace", "explain"],
    "case": ["case_study", "explain"],
    "definition": ["explain", "flashcards"],
}

DOMAIN_PACKS = {
    "economics": {"prefer": ["interactive_sim", "case_study"]},
    "psychology": {"prefer": ["case_study", "interactive_sim"]},  # experiment-driven books: replay the study, then a live demo
    "investing": {"prefer": ["case_study", "diagram"]},
    "math_physics": {"prefer": ["manim", "interactive_sim"]},
    "history": {"prefer": ["timeline", "story"]},
    "programming": {"prefer": ["code_trace"]},
    "language": {"prefer": ["flashcards"]},
    "general": {"prefer": []},
}


def choose_tools(kind: str, form: str, domain: str = "general", mastery: float = 0.0,
                 prefers_visual: bool = True) -> list[str]:
    tools = list(_BY_FORM.get(form, ["explain"]))
    if kind == "memory" and "flashcards" not in tools:
        tools.append("flashcards")
    for t in DOMAIN_PACKS.get(domain, {}).get("prefer", []):
        if t in tools:
            tools.remove(t)
            tools.insert(0, t)
    if not prefers_visual:
        tools.sort(key=lambda t: t != "explain")
    if mastery >= 0.8:  # already known: skip heavy visuals, go to application
        tools = ["case_study" if kind in ("concept", "design") else "flashcards"]
    if "explain" not in tools:
        tools.append("explain")
    return tools
