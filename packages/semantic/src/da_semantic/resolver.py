"""指标直连解析器：问题 → 预定义指标的确定性匹配。

三层递进（确定性逐层下降、覆盖面逐层扩大）：
1. 名称/别名子串命中（score=1.0，判决性）——"6月成交额" 直接命中 GMV
2. 定义文本 n-gram 相似（提名性，过阈值才给）
3. 未命中 → 交给 LLM 语义匹配兜底（prompt 注入的全量指标区块）

大规模指标场景下，本解析器同时充当检索层：top-k 命中的指标置顶注入，
避免几百个指标撑爆上下文。
"""

from __future__ import annotations

from dataclasses import dataclass

from da_platform.vector import NgramIndex

from da_semantic.model import Metric
from da_semantic.store import SemanticStore

NGRAM_THRESHOLD = 0.35  # 定义文本相似仅做提名，阈值宽松；判决靠子串命中


@dataclass
class MetricMatch:
    metric: Metric
    score: float
    matched_by: str  # name | alias | definition


class MetricResolver:
    def __init__(self, store: SemanticStore) -> None:
        self._store = store

    async def _load_metrics(self) -> list[Metric]:
        metrics = []
        for name in await self._store.list_names("metric"):
            record = await self._store.get("metric", name)
            if record is not None:
                metrics.append(Metric.model_validate(record.payload))
        return metrics

    async def resolve(self, question: str, top_k: int = 3) -> list[MetricMatch]:
        """返回按置信度降序的指标命中；空列表 = 交给 LLM 兜底。"""
        metrics = await self._load_metrics()
        if not metrics:
            return []
        q = question.lower()

        matches: list[MetricMatch] = []
        remaining: list[Metric] = []
        for m in metrics:
            if m.name.lower() in q:
                matches.append(MetricMatch(metric=m, score=1.0, matched_by="name"))
            elif any(a and a.lower() in q for a in m.aliases):
                matches.append(MetricMatch(metric=m, score=1.0, matched_by="alias"))
            else:
                remaining.append(m)

        if remaining and len(matches) < top_k:
            index = NgramIndex()
            for m in remaining:
                index.add(m.name, f"{m.name} {' '.join(m.aliases)} {m.definition}")
            by_name = {m.name: m for m in remaining}
            for key, score in index.search(question, top_k=top_k - len(matches)):
                if score >= NGRAM_THRESHOLD:
                    matches.append(
                        MetricMatch(metric=by_name[key], score=round(score, 3),
                                    matched_by="definition")
                    )
        matches.sort(key=lambda x: -x.score)
        return matches[:top_k]
