"""BM25 keyword index over name, description, tags, category and municipality (PlacesLayer.md 5.7)."""
from __future__ import annotations

import math
from collections import Counter

from .text import tokens

K1, B = 1.5, 0.75


def doc_text(p) -> str:
    return " ".join([p.name, p.name, p.description, " ".join(p.tags), " ".join(p.cuisine), p.category,
                     p.municipality])


class BM25:
    def __init__(self, docs: dict[str, str]):
        self.tf = {i: Counter(tokens(t)) for i, t in docs.items()}
        self.len = {i: sum(c.values()) for i, c in self.tf.items()}
        self.avg = (sum(self.len.values()) / len(self.len)) if self.len else 0.0
        df = Counter()
        for c in self.tf.values():
            df.update(c.keys())
        n = len(self.tf)
        self.idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}

    def scores(self, query: str) -> dict[str, float]:
        q = tokens(query)
        out = {}
        for i, tf in self.tf.items():
            s = 0.0
            for t in q:
                f = tf.get(t, 0)
                if f:
                    s += self.idf[t] * f * (K1 + 1) / (f + K1 * (1 - B + B * self.len[i] / (self.avg or 1)))
            if s > 0:
                out[i] = s
        return out

    def search(self, query: str, k: int = 10) -> list[tuple[str, float]]:
        r = sorted(self.scores(query).items(), key=lambda kv: (-kv[1], kv[0]))
        return r[:k]
