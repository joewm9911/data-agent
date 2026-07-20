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

    async def _merge_into_store(
        self, left: tuple, right: tuple, actor: str
    ) -> None:
        """把确认过的列对归一进语义层：并入已含任一绑定的实体，否则新建实体。"""
        from da_semantic.model import Binding, Entity, JoinPath

        target_name = None
        for name in await self._store.list_names("entity"):
            record = await self._store.get("entity", name)
            if record is None:
                continue
            bound = {(b["table"], b["column"]) for b in record.payload.get("bindings", [])}
            if left in bound or right in bound:
                target_name = name
                break

        join = JoinPath(
            expr=f"{left[0]}.{left[1]} = {right[0]}.{right[1]}",
            evidence="human", confidence=1.0,
        )
        if target_name is not None:
            record = await self._store.get("entity", target_name)
            assert record is not None
            entity = Entity.model_validate(record.payload)
            bound = {(b.table, b.column) for b in entity.bindings}
            for t, col in (left, right):
                if (t, col) not in bound:
                    entity.bindings.append(Binding(table=t, column=col))
            if join.expr not in {j.expr for j in entity.join_paths}:
                entity.join_paths.append(join)
            await self._store.put("entity", target_name, entity.model_dump(), actor)
        else:
            entity = Entity(
                name=f"实体_{left[1]}",
                canonical_key=left[1],
                bindings=[Binding(table=left[0], column=left[1]),
                          Binding(table=right[0], column=right[1])],
                join_paths=[join],
            )
            await self._store.put("entity", entity.name, entity.model_dump(), actor)

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

        if item.kind == "entity_merge":
            left = tuple(item.context["left"])
            right = tuple(item.context["right"])
            if choice.startswith("是"):
                if self._graph is not None:
                    self._graph.add_edge(
                        EvidenceEdge(left=left, right=right, kind="human_confirm",
                                     score=1.0, detail=f"人工确认 by {actor}")
                    )
                # 确认即归一：判决直接写入语义层（不止留在临时证据图）
                await self._merge_into_store(left, right, actor)
            else:
                if self._graph is not None:
                    self._graph.add_veto(left, right)
                # 反例持久化，永不复发（4.5）
                from da_semantic.model import CounterExample

                counter = CounterExample(
                    kind="bad_join",
                    expr=f"{left[0]}.{left[1]} = {right[0]}.{right[1]}",
                    reason=f"人工否决 by {actor}",
                )
                await self._store.put(
                    "counter_example",
                    f"veto:{left[0]}.{left[1]}={right[0]}.{right[1]}",
                    counter.model_dump(), actor,
                )
        elif item.kind == "metric_caliber":  # noqa: SIM114 - 分支语义不同
            record = await self._store.get("metric", item.context["metric"])
            if record is not None:
                payload = dict(record.payload)
                payload["expr"] = choice
                payload["verified"] = True
                await self._store.put("metric", item.context["metric"], payload, actor)
        return item
