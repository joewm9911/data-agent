"""学习回路（架构文档 4.5）：澄清即沉淀 / 纠正即训练 / 冲突显式化 / verified answers 生长。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from da_semantic.model import CounterExample, VerifiedAnswer
from da_semantic.store import SemanticStore


@dataclass
class CaliberConflict:
    """跨部门口径矛盾：不静默选边，暴露给数据负责人裁决（治理卖点）。"""

    metric_name: str
    variants: list[tuple[str, str]]  # (expr, 来源/使用方)


class LearningLoop:
    def __init__(self, store: SemanticStore) -> None:
        self._store = store

    async def record_clarification(
        self, alias: str, entity_name: str, actor: str
    ) -> None:
        """澄清即沉淀：用户答"'会员'就是客户" → 别名写入，同一问题永不再问。"""
        record = await self._store.get("entity", entity_name)
        if record is None:
            return
        payload = dict(record.payload)
        aliases = list(payload.get("aliases", []))
        if alias not in aliases:
            aliases.append(alias)
            payload["aliases"] = aliases
            await self._store.put("entity", entity_name, payload, actor)

    async def record_correction(
        self,
        metric_name: str,
        wrong_expr: str,
        corrected_expr: str,
        reason: str,
        actor: str,
    ) -> None:
        """纠正即训练：口径修正版本化写回 + 反例入库防复发。"""
        record = await self._store.get("metric", metric_name)
        if record is not None:
            payload = dict(record.payload)
            payload["expr"] = corrected_expr
            payload["verified"] = True
            await self._store.put("metric", metric_name, payload, actor)
        counter = CounterExample(kind="bad_expr", expr=wrong_expr, reason=reason)
        await self._store.put(
            "counter_example",
            f"{metric_name}:{hash(wrong_expr) & 0xFFFFFFFF:08x}",
            counter.model_dump(),
            actor,
        )

    async def record_verified_answer(
        self, question: str, sql_template: str, actor: str, restricted: bool = False
    ) -> None:
        """verified answers 生长：复用 SQL 模板，以提问者身份重执行（6.1）。"""
        answer = VerifiedAnswer(
            question=question,
            sql_template=sql_template,
            verified_by=actor,
            verified_at=datetime.now(UTC),
            restricted=restricted,
        )
        await self._store.put(
            "verified_answer", f"va:{hash(question) & 0xFFFFFFFF:08x}",
            answer.model_dump(mode="json"), actor,
        )

    async def find_verified_answer(self, question: str) -> VerifiedAnswer | None:
        """M1 相似度：规范化精确匹配；M2 升级为 embedding 召回。"""
        key = f"va:{hash(question) & 0xFFFFFFFF:08x}"
        record = await self._store.get("verified_answer", key)
        return VerifiedAnswer.model_validate(record.payload) if record else None

    async def detect_conflicts(self) -> list[CaliberConflict]:
        """冲突显式化：同名指标的历史版本出现互异 expr 且都被 verified 过。"""
        conflicts: list[CaliberConflict] = []
        for name in await self._store.list_names("metric"):
            history = await self._store.history("metric", name)
            verified_exprs: dict[str, str] = {}
            for record in history:
                if record.payload.get("verified"):
                    verified_exprs[record.payload["expr"]] = record.updated_by
            if len(verified_exprs) > 1:
                conflicts.append(
                    CaliberConflict(
                        metric_name=name,
                        variants=[(e, by) for e, by in verified_exprs.items()],
                    )
                )
        return conflicts
