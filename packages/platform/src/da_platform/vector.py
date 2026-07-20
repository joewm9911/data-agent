"""向量检索抽象（10.2 VectorIndex）+ 零依赖 n-gram 余弦实现。

字符 2-gram 对中文问句效果好且无模型依赖；生产可替换为 pgvector/embedding provider
（同一接口，同一测试）。
"""

from __future__ import annotations

import math
from collections import Counter
from typing import Protocol


class VectorIndex(Protocol):
    def add(self, key: str, text: str) -> None: ...

    def search(self, text: str, top_k: int = 3) -> list[tuple[str, float]]:
        """返回 [(key, 相似度)]，相似度 ∈ [0,1] 降序。"""
        ...


def _ngrams(text: str, n: int = 2) -> Counter:
    text = "".join(text.split()).lower()
    if len(text) < n:
        return Counter([text] if text else [])
    return Counter(text[i : i + n] for i in range(len(text) - n + 1))


def cosine(a: Counter, b: Counter) -> float:
    if not a or not b:
        return 0.0
    dot = sum(v * b[k] for k, v in a.items())
    na = math.sqrt(sum(v * v for v in a.values()))
    nb = math.sqrt(sum(v * v for v in b.values()))
    return dot / (na * nb) if na and nb else 0.0


class NgramIndex:
    def __init__(self, n: int = 2) -> None:
        self._n = n
        self._vectors: dict[str, Counter] = {}

    def add(self, key: str, text: str) -> None:
        self._vectors[key] = _ngrams(text, self._n)

    def search(self, text: str, top_k: int = 3) -> list[tuple[str, float]]:
        query = _ngrams(text, self._n)
        scored = [
            (key, cosine(query, vec)) for key, vec in self._vectors.items()
        ]
        scored.sort(key=lambda x: -x[1])
        return scored[:top_k]
