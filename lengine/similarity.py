"""Answer similarity for short typed answers.

Python port of the idea in OpenLingo `lib/similarity.ts` (MIT, Pretzel AI GmbH): normalise case,
diacritics, punctuation and whitespace, then score by character edit distance.
"""
from __future__ import annotations

import re
import unicodedata


def normalize(s: str) -> str:
    s = unicodedata.normalize("NFKD", s.lower().replace("ß", "ss"))
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = re.sub(r"[.,;:!?¿¡\"'“”‘’()\[\]（），。；：！？、]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def levenshtein(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for jdx, cb in enumerate(b, 1):
            cur.append(min(prev[jdx] + 1, cur[jdx - 1] + 1, prev[jdx - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def similarity(user: str, correct: str) -> float:
    a, b = normalize(user), normalize(correct)
    if not a and not b:
        return 1.0
    return 1 - levenshtein(a, b) / max(len(a), len(b))
