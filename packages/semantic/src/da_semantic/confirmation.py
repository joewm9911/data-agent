"""确认队列（架构文档 4.4）：考试式选择题，按幂律排序，30 分钟覆盖 80% 日常提问。

每个待确认项是一道选择题；答案写回语义层（verified）或证据图（veto 反例）。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Literal

from da_semantic.evidence import EvidenceEdge, EvidenceGraph
from da_semantic.store import SemanticStore


@dataclass
class ConfirmationItem:
    item_id: str
    kind: Literal["entity_merge", "metric_caliber", "enum_meaning"]
    question: str
    options: list[str]
    context: dict = field(default_factory=dict)
    priority: int = 0  # 幂律排序：表热度/指标查询频次
    status: Literal["pending", "answered", "skipped"] = "pending"
    answer: str | None = None


class ConfirmationQueue:
    def __init__(self, store: SemanticStore, graph: EvidenceGraph | None = None) -> None:
        self._store = store
        self._graph = graph
        self._items: dict[str, ConfirmationItem] = {}

    def add_entity_merge(self, edge: EvidenceEdge, priority: int = 0) -> ConfirmationItem:
        lhs, rhs = edge.left, edge.right
        item = ConfirmationItem(
            item_id=uuid.uuid4().hex,
            kind="entity_merge",
            question=(
                f"{lhs[0]}.{lhs[1]} 和 {rhs[0]}.{rhs[1]} 是同一个业务实体的 ID 吗？"
                f"（证据：{edge.detail}）"
            ),
            options=["是，同一实体", "否，不同实体"],
            context={"left": list(lhs), "right": list(rhs), "confidence": edge.score},
            priority=priority,
        )
        self._items[item.item_id] = item
        return item

    def add_metric_caliber(
        self, metric_name: str, candidates: list[str], priority: int = 0
    ) -> ConfirmationItem:
        item = ConfirmationItem(
            item_id=uuid.uuid4().hex,
            kind="metric_caliber",
            question=f"指标「{metric_name}」发现多种算法，哪个是标准口径？",
            options=candidates,
            context={"metric": metric_name},
            priority=priority,
        )
        self._items[item.item_id] = item
        return item

    def pending(self) -> list[ConfirmationItem]:
        """按优先级降序（幂律：最常用的先确认）。"""
        return sorted(
            (i for i in self._items.values() if i.status == "pending"),
            key=lambda i: -i.priority,
        )

    async def answer(self, item_id: str, choice: str, actor: str) -> ConfirmationItem:
        item = self._items[item_id]
        item.status = "answered"
        item.answer = choice

        if item.kind == "entity_merge" and self._graph is not None:
            left = tuple(item.context["left"])
            right = tuple(item.context["right"])
            if choice.startswith("是"):
                self._graph.add_edge(
                    EvidenceEdge(left=left, right=right, kind="human_confirm",
                                 score=1.0, detail=f"人工确认 by {actor}")
                )
            else:
                self._graph.add_veto(left, right)  # 反例入库，永不复发（4.5）
        elif item.kind == "metric_caliber":
            record = await self._store.get("metric", item.context["metric"])
            if record is not None:
                payload = dict(record.payload)
                payload["expr"] = choice
                payload["verified"] = True
                await self._store.put("metric", item.context["metric"], payload, actor)
        return item
