"""证据图与实体归一（架构文档 4.3）。

列为节点、证据为带权边；按置信度三层处理：
高置信自动合并 / 中置信进确认队列 / 低置信不猜（铁律 P2）。
归一粒度是 (table, column) 二元组——防同名不同义。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from difflib import SequenceMatcher

from da_semantic.mining import JoinEvidence
from da_semantic.profiling import ColumnProfile, OverlapEvidence

# 证据强度权重（4.3：查询日志判决性 > 值域 > 血缘 > 列名仅提名）
WEIGHT_QUERY_LOG = 0.95
WEIGHT_VALUE_OVERLAP = 0.85
WEIGHT_LINEAGE = 0.80
WEIGHT_NAME_SIMILARITY = 0.35  # 只做候选召回，永不判决

AUTO_MERGE_THRESHOLD = 0.90
CONFIRM_THRESHOLD = 0.50

ColumnRef = tuple[str, str]  # (table, column)


@dataclass
class EvidenceEdge:
    left: ColumnRef
    right: ColumnRef
    kind: str  # query_log | value_overlap | lineage | name_similarity | human_confirm
    score: float
    detail: str = ""


@dataclass
class EntityCluster:
    members: list[ColumnRef]
    confidence: float
    evidences: list[EvidenceEdge] = field(default_factory=list)


@dataclass
class UnificationResult:
    auto_clusters: list[EntityCluster] = field(default_factory=list)
    to_confirm: list[EvidenceEdge] = field(default_factory=list)  # 中置信 → 确认队列
    rejected: list[EvidenceEdge] = field(default_factory=list)


class EvidenceGraph:
    def __init__(self) -> None:
        self._edges: dict[tuple[ColumnRef, ColumnRef], list[EvidenceEdge]] = {}
        self._vetoes: set[tuple[ColumnRef, ColumnRef]] = set()  # 反例：判决性否定

    @staticmethod
    def _key(a: ColumnRef, b: ColumnRef) -> tuple[ColumnRef, ColumnRef]:
        return tuple(sorted([a, b]))  # type: ignore[return-value]

    def add_edge(self, edge: EvidenceEdge) -> None:
        self._edges.setdefault(self._key(edge.left, edge.right), []).append(edge)

    def add_veto(self, a: ColumnRef, b: ColumnRef) -> None:
        """反例与正例同权重（4.5）：人工否定后该对永不再合并、不再进确认队列。"""
        self._vetoes.add(self._key(a, b))

    def add_join_evidence(self, joins: list[JoinEvidence]) -> None:
        for j in joins:
            self.add_edge(
                EvidenceEdge(
                    left=j.left, right=j.right, kind="query_log",
                    score=WEIGHT_QUERY_LOG, detail=f"历史查询中 join 过 {j.count} 次",
                )
            )

    def add_overlap_evidence(self, overlaps: list[OverlapEvidence]) -> None:
        for o in overlaps:
            self.add_edge(
                EvidenceEdge(
                    left=o.left, right=o.right, kind="value_overlap",
                    score=WEIGHT_VALUE_OVERLAP * o.containment,
                    detail=f"值域包含度 {o.containment:.0%}",
                )
            )

    def add_name_similarity(self, profiles: list[ColumnProfile], min_ratio: float = 0.7) -> None:
        cols = [(p.table, p.column) for p in profiles]
        for i, a in enumerate(cols):
            for b in cols[i + 1 :]:
                if a[0] == b[0]:
                    continue
                ratio = SequenceMatcher(None, a[1], b[1]).ratio()
                if ratio >= min_ratio:
                    self.add_edge(
                        EvidenceEdge(
                            left=a, right=b, kind="name_similarity",
                            score=WEIGHT_NAME_SIMILARITY * ratio,
                            detail=f"列名相似度 {ratio:.0%}",
                        )
                    )

    def pair_confidence(self, a: ColumnRef, b: ColumnRef) -> float:
        """多证据聚合：1 - Π(1 - score)。名字相似单独出现永远到不了确认线以上。"""
        edges = self._edges.get(self._key(a, b), [])
        if not edges:
            return 0.0
        miss = 1.0
        for e in edges:
            miss *= 1.0 - e.score
        return 1.0 - miss

    def unify(self) -> UnificationResult:
        result = UnificationResult()
        parent: dict[ColumnRef, ColumnRef] = {}

        def find(x: ColumnRef) -> ColumnRef:
            parent.setdefault(x, x)
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def union(a: ColumnRef, b: ColumnRef) -> None:
            parent[find(a)] = find(b)

        pair_conf: dict[tuple[ColumnRef, ColumnRef], float] = {}
        for (a, b), edges in self._edges.items():
            if (a, b) in self._vetoes:
                result.rejected.extend(edges)
                continue
            conf = self.pair_confidence(a, b)
            pair_conf[(a, b)] = conf
            if conf >= AUTO_MERGE_THRESHOLD:
                union(a, b)
            elif conf >= CONFIRM_THRESHOLD:
                best = max(edges, key=lambda e: e.score)
                result.to_confirm.append(
                    EvidenceEdge(
                        left=a, right=b, kind=best.kind, score=round(conf, 3),
                        detail="; ".join(e.detail for e in edges),
                    )
                )
            else:
                result.rejected.extend(edges)

        clusters: dict[ColumnRef, list[ColumnRef]] = {}
        for node in parent:
            clusters.setdefault(find(node), []).append(node)
        for members in clusters.values():
            if len(members) < 2:
                continue
            confs = [
                pair_conf.get(self._key(a, b), 0.0)
                for i, a in enumerate(members)
                for b in members[i + 1 :]
                if pair_conf.get(self._key(a, b), 0.0) > 0
            ]
            result.auto_clusters.append(
                EntityCluster(
                    members=sorted(members),
                    confidence=round(min(confs), 3) if confs else 0.0,
                    evidences=[
                        e
                        for i, a in enumerate(members)
                        for b in members[i + 1 :]
                        for e in self._edges.get(self._key(a, b), [])
                    ],
                )
            )
        result.auto_clusters.sort(key=lambda c: -c.confidence)
        return result
