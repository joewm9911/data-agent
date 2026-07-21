"""学习回路（架构文档 4.5）：澄清即沉淀 / 纠正即训练 / 冲突显式化 / verified answers 生长。

verified answers 召回：n-gram 向量模糊匹配（"6月的GMV" 命中 "6月GMV是多少"），
生产可换 embedding provider（同 VectorIndex 接口）。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from da_platform.vector import NgramIndex, VectorIndex

from da_semantic.model import CounterExample, VerifiedAnswer
from da_semantic.store import SemanticStore

# n-gram 特征下的经验阈值；升级 embedding provider 后应重标定
SIMILARITY_THRESHOLD = 0.55


@dataclass
class CaliberConflict:
    """跨部门口径矛盾：不静默选边，暴露给数据负责人裁决（治理卖点）。"""

    metric_name: str
    variants: list[tuple[str, str]]  # (expr, 来源/使用方)


class LearningLoop:
    def __init__(self, store: SemanticStore, index: VectorIndex | None = None) -> None:
        self._store = store
        self._index = index or NgramIndex()
        self._index_loaded = False

    async def _ensure_index(self) -> None:
        """首次使用时从存储重建索引（进程重启后召回不丢，持久化在 store）。"""
        if self._index_loaded:
            return
        for name in await self._store.list_names("verified_answer"):
            record = await self._store.get("verified_answer", name)
            if record is not None:
                self._index.add(name, record.payload.get("question", ""))
        self._index_loaded = True

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

    async def record_metric_alias(
        self, alias: str, metric_name: str, actor: str
    ) -> None:
        """指标别名澄清即沉淀："'成交额'就是 GMV" → 之后指标直连判决性命中。"""
        record = await self._store.get("metric", metric_name)
        if record is None:
            return
        payload = dict(record.payload)
        aliases = list(payload.get("aliases", []))
        if alias not in aliases:
            aliases.append(alias)
            payload["aliases"] = aliases
            await self._store.put("metric", metric_name, payload, actor)

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
        await self._ensure_index()
        key = f"va:{hash(question) & 0xFFFFFFFF:08x}"
        await self._store.put(
            "verified_answer", key, answer.model_dump(mode="json"), actor
        )
        self._index.add(key, question)

    async def find_verified_answer(self, question: str) -> VerifiedAnswer | None:
        """向量模糊召回 + 数字令牌守卫。

        相似度过阈值才命中（宁可不猜，铁律 P2）；问题中的数字（月份/年份/ID）
        必须完全一致——"7月GMV"绝不能复用"6月GMV"的答案。
        """
        import re

        await self._ensure_index()
        query_numbers = set(re.findall(r"\d+", question))
        for key, score in self._index.search(question, top_k=3):
            if score < SIMILARITY_THRESHOLD:
                break
            record = await self._store.get("verified_answer", key)
            if record is None:
                continue
            candidate_numbers = set(
                re.findall(r"\d+", record.payload.get("question", ""))
            )
            # 子集规则：允许省略（"6月"命中"2026年6月"），拒绝冲突（"7月"≠"6月"）
            compatible = (
                query_numbers <= candidate_numbers
                or candidate_numbers <= query_numbers
            )
            if not compatible:
                continue
            return VerifiedAnswer.model_validate(record.payload)
        return None

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
