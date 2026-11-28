"""Scripted stand-in for a model, used ONLY to test plumbing. Not a quality result."""
import json
import re
import zlib

import numpy as np

SIM = """<!doctype html><html><body><h1>需求与供给</h1><label>需求移动 <input type="range" id="d" min="-50" max="50" value="0"></label>
<svg width="300" height="200"><line id="dl" x1="20" y1="20" x2="280" y2="180" stroke="blue"/></svg><p id="eq"></p>
<div>简化说明：直线需求曲线。</div><div>来源：p.30</div>
<script>document.getElementById('d').addEventListener('input',e=>{document.getElementById('eq').textContent='移动 '+e.target.value})</script></body></html>"""

class FakeLLM:
    def __init__(self):
        self.calls = []
    def complete(self, system, user, purpose="chat", json_mode=False):
        self.calls.append(purpose)
        if purpose == "knowledge":
            return json.dumps({"concepts": [
                {"key": "demand", "name": "需求", "summary": "价格与需求量的关系", "kind": "concept", "form": "parametric", "requires": [], "pages": [24, 25]},
                {"key": "supply", "name": "供给", "summary": "价格与供给量的关系", "kind": "concept", "form": "parametric", "requires": [], "pages": [26]},
                {"key": "equilibrium", "name": "均衡", "summary": "需求量等于供给量", "kind": "design", "form": "parametric", "requires": ["demand", "supply"], "pages": [28]},
                {"key": "uncited", "name": "无引用", "summary": "x", "kind": "concept", "form": "definition", "pages": [999]}]})
        if purpose == "teach":
            if "Learner:" in user:
                return json.dumps({"reply": "对，(p.24)", "understood": True, "next_question": "若收入上升？", "pages": [24]})
            return json.dumps({"explanation": "需求定律 (p.24)", "check_question": "为什么向下倾斜？", "expected_points": ["替代"], "pages": [24]})
        if purpose == "practice":
            return json.dumps({"questions": [
                {"qtype": "single_choice", "level": "recall", "prompt": "需求定律说？", "choices": ["A 价格升需求量降", "B 价格升需求量升"], "answer": "A", "pages": [24]},
                {"qtype": "true_false", "level": "understand", "prompt": "需求变化等于需求量变化", "answer": "false", "pages": [25]},
                {"qtype": "application", "level": "apply", "prompt": "汽油价格上涨后…", "answer": "需求量下降", "rubric": "沿曲线移动", "pages": [28]}]})
        if purpose == "grading":
            return json.dumps({"score": 0.5, "error_type": "cannot_transfer", "feedback": "提示：是沿曲线移动还是曲线移动？", "pages": [25]})
        if purpose == "visual":
            return json.dumps({"title": "供需", "html": SIM, "claims": ["需求向下倾斜 (p.24)"]})
        if purpose == "chapter":
            pages = [int(x) for x in re.findall(r"\[p\.(\d+)\]", user)[:2]] or [1]
            return json.dumps({"summary": "本章讲需求与供给。", "core_ideas": [{"text": "价格影响需求量", "pages": pages}],
                               "arguments": [{"claim": "需求定律", "evidence": "实例", "pages": [9999]}],
                               "cases": [{"name": "汽油价格", "point": "价格上升需求量下降", "pages": pages}]})
        if purpose == "relations":
            out = []
            for n, a, b in re.findall(r"### Pair (\d+)\n.*?passage A \[p\.\d+\]: (.*?)\n\nB: .*?passage B \[p\.\d+\]: (.*?)(?=\n\n### Pair|\Z)", user, re.S):
                n = int(n)
                bad = n == 2  # one invented quote per batch: must be discarded by the program
                out.append({"id": n, "relation": "extends" if n % 2 else "similar_to", "direction": "B->A", "confidence": 0.9,
                            "rationale": "A 说…；B 说…", "quote_a": a.strip()[:14], "quote_b": "原文里没有这句话的引用" if bad else b.strip()[:14]})
            return json.dumps({"pairs": out})
        if purpose == "crossbook":
            ps = dict(re.findall(r"\[(S\d+)\] [^\n]*\n([^\n]+)", user))
            first = next(iter(ps))
            return json.dumps({"by_book": [{"book": "Econ", "views": [{"text": "作者认为需求向下倾斜", "cites": [first], "quote": ps[first].strip()[:20]}]}],
                               "synthesis": [{"text": "两本书一致", "cites": [first], "quote": "这句不在原文里面"}], "gaps": ""})
        raise AssertionError(purpose)


class FakeEmbedder:
    """Deterministic bag-of-bigrams vectors: equal names give equal vectors, so candidates are predictable."""
    def _vec(self, text):
        v = np.zeros(1024, dtype=np.float32)
        t = re.sub(r"\s+", "", text.split("：")[0])
        for i in range(max(1, len(t) - 1)):
            v[zlib.crc32(t[i:i + 2].encode()) % 1024] += 1
        return v / (np.linalg.norm(v) or 1)

    def passages(self, texts):
        return np.array([self._vec(t) for t in texts], dtype=np.float32)

    def query(self, text):
        return self._vec(text.split("Query:")[-1])
