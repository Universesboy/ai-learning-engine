"""Model layer. Default provider: Claude (Anthropic SDK). Also any OpenAI-compatible API (DeepSeek, Qwen, Ollama...).

Models are chosen per purpose, like OpenMAIC's slots, so cheap models teach and strong models build
the knowledge map. Every call is logged to `llm_usage` for the cost report.
Claude env: ANTHROPIC_API_KEY; models default to claude-haiku-5-5 (cheapest); override per purpose with
LLM_MODEL_<PURPOSE>, e.g. LLM_MODEL_KNOWLEDGE=claude-sonnet-5-5.
OpenAI-compatible env: LLM_PROVIDER=openai, LLM_API_KEY, LLM_BASE_URL, LLM_MODEL.
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
from typing import Protocol

from json_repair import repair_json


class LLM(Protocol):
    def complete(self, system: str, user: str, purpose: str = "chat", json_mode: bool = False) -> str: ...


class OpenAICompatLLM:
    def __init__(self, con: sqlite3.Connection | None = None):
        from openai import OpenAI
        key = os.environ.get("LLM_API_KEY")
        if not key:
            raise RuntimeError("LLM_API_KEY is not set")
        self.client = OpenAI(api_key=key, base_url=os.environ.get("LLM_BASE_URL", "https://api.deepseek.com"))
        self.default = os.environ.get("LLM_MODEL", "deepseek-flash")
        self.con = con

    def model_for(self, purpose: str) -> str:
        return os.environ.get(f"LLM_MODEL_{purpose.upper()}", self.default)

    def complete(self, system: str, user: str, purpose: str = "chat", json_mode: bool = False) -> str:
        model = self.model_for(purpose)
        t0 = time.time()
        kwargs = {"response_format": {"type": "json_object"}} if json_mode else {}
        resp = self.client.chat.completions.create(
            model=model, messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            temperature=0.3, **kwargs)
        if self.con is not None and resp.usage:
            self.con.execute(
                "INSERT INTO llm_usage(purpose, model, prompt_tokens, completion_tokens, seconds) VALUES (?,?,?,?,?)",
                (purpose, model, resp.usage.prompt_tokens, resp.usage.completion_tokens, time.time() - t0))
            self.con.commit()
        return resp.choices[0].message.content or ""


# USD per million tokens (input, output), Claude API list prices cached 2026-11-03.
# Haiku 5.5 uses the higher card when a prompt exceeds 100K tokens.
PRICES = {
    "claude-haiku-5-5": (0.10, 0.50), "claude-haiku-5-5>100k": (0.50, 2.50),
    "claude-sonnet-5-5": (2.00, 10.00), "claude-opus-5-5": (4.00, 20.00),
}
# Effort per purpose: thinking tokens are billed as output, so routine steps run at low effort.
EFFORT = {"knowledge": "medium", "visual": "medium", "teach": "low", "practice": "low", "grading": "low"}


def cost_usd(model: str, prompt_tokens: int, completion_tokens: int) -> float | None:
    key = f"{model}>100k" if model == "claude-haiku-5-5" and prompt_tokens > 100_000 else model
    if key not in PRICES:
        return None
    pin, pout = PRICES[key]
    return (prompt_tokens * pin + completion_tokens * pout) / 1e6


class ClaudeLLM:
    def __init__(self, con: sqlite3.Connection | None = None):
        import anthropic
        # CLAUDE_API_KEY is accepted too: some hosts (e.g. Claude Code cloud sessions) hide ANTHROPIC_API_KEY.
        self.client = anthropic.Anthropic(api_key=claude_key())
        self.default = os.environ.get("LLM_MODEL", "claude-haiku-5-5")
        self.con = con

    # Agreed setup: Haiku 5.5 everywhere, Sonnet 5.5 for the knowledge map (everything downstream depends on it).
    PURPOSE_DEFAULTS = {"knowledge": "claude-sonnet-5-5"}

    def model_for(self, purpose: str) -> str:
        return os.environ.get(f"LLM_MODEL_{purpose.upper()}", self.PURPOSE_DEFAULTS.get(purpose, self.default))

    def complete(self, system: str, user: str, purpose: str = "chat", json_mode: bool = False) -> str:
        model = self.model_for(purpose)
        if json_mode:
            system += "\n\nRespond with ONE JSON object and nothing else."
        t0 = time.time()
        with self.client.messages.stream(
            model=model, max_tokens=32000, system=system,
            messages=[{"role": "user", "content": user}],
            output_config={"effort": EFFORT.get(purpose, "low")},
        ) as stream:
            resp = stream.get_final_message()
        if resp.stop_reason == "refusal":
            raise RuntimeError(f"model declined the {purpose} request")
        if self.con is not None:
            u = resp.usage
            prompt = (u.input_tokens or 0) + (u.cache_read_input_tokens or 0) + (u.cache_creation_input_tokens or 0)
            self.con.execute(
                "INSERT INTO llm_usage(purpose, model, prompt_tokens, completion_tokens, seconds) VALUES (?,?,?,?,?)",
                (purpose, model, prompt, u.output_tokens, time.time() - t0))
            self.con.commit()
        return "".join(b.text for b in resp.content if b.type == "text")


def claude_key() -> str | None:
    return os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("CLAUDE_API_KEY")


def make_llm(con: sqlite3.Connection | None = None) -> LLM:
    provider = os.environ.get("LLM_PROVIDER", "anthropic" if claude_key() or not os.environ.get("LLM_API_KEY") else "openai")
    if provider == "anthropic":
        if not claude_key():
            raise RuntimeError("ANTHROPIC_API_KEY (or CLAUDE_API_KEY) is not set")
        return ClaudeLLM(con)
    return OpenAICompatLLM(con)


def complete_json(llm: LLM, system: str, user: str, purpose: str, retries: int = 2) -> dict:
    """Ask for JSON, repair near-misses (json-repair, same approach as DeepTutor/OpenMAIC), retry otherwise."""
    last = ""
    for _ in range(retries + 1):
        last = llm.complete(system, user, purpose=purpose, json_mode=True)
        try:
            data = json.loads(repair_json(last))
            if isinstance(data, dict):
                return data
        except Exception:
            pass
        user += "\n\nReturn ONE valid JSON object only."
    raise ValueError(f"model did not return JSON for {purpose}: {last[:200]}")
