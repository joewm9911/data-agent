"""LLM 语义增强器（4.2 信号四/五 + 语义维护提效）。

用大模型补齐确定性信号覆盖不到的部分：
- 列描述推断（信号五：LLM 兜底，"ev_ts 是事件时间戳还是电视剧评分"）
- 枚举值业务含义猜测 → 生成确认题（人只点选择题，不填空）
- 文档/wiki 口径挖掘（信号四）
- 实体簇命名（"实体_1" → "客户"）

LLM 经 CompleteFn 注入（Callable[[prompt], Awaitable[str]]）——语义层不依赖具体
模型客户端（铁律 P5）；生产由 da_agent.llm_bridge 提供 MiniMax/Claude 实现。
所有 LLM 输出只作草稿/确认题，绝不直接成为 verified 口径（铁律 P2）。
"""

from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from da_types import CatalogSnapshot

from da_semantic.confirmation import ConfirmationItem, ConfirmationQueue
from da_semantic.evidence import EntityCluster
from da_semantic.profiling import ColumnProfile

CompleteFn = Callable[[str], Awaitable[str]]


def _extract_json(text: str) -> dict | list:
    """从 LLM 输出中提取 JSON（容忍 markdown 代码块与前后缀文本）。"""
    match = re.search(r"```(?:json)?\s*([\[{].*?[\]}])\s*```", text, re.DOTALL)
    payload = match.group(1) if match else text
    start = min(
        (i for i in (payload.find("{"), payload.find("[")) if i >= 0), default=0
    )
    end = max(payload.rfind("}"), payload.rfind("]")) + 1
    return json.loads(payload[start:end])


@dataclass
class EnrichmentResult:
    column_descriptions: dict[tuple[str, str], str] = field(default_factory=dict)
    entity_names: dict[str, str] = field(default_factory=dict)  # 簇 key → 建议名
    enum_questions: list[ConfirmationItem] = field(default_factory=list)
    doc_metric_drafts: dict[str, dict] = field(default_factory=dict)


class SemanticEnricher:
    def __init__(self, complete: CompleteFn) -> None:
        self._complete = complete

    async def describe_columns(
        self, catalog: CatalogSnapshot, profiles: list[ColumnProfile]
    ) -> dict[tuple[str, str], str]:
        """信号五：从表/列名+采样值推断中文业务描述（草稿，进上下文不进 verified）。"""
        samples = {
            (p.table, p.column): sorted(p.samples)[:5] for p in profiles
        }
        tables_desc = []
        for t in catalog.tables:
            cols = ", ".join(
                f"{c.name}(样例:{samples.get((t.name, c.name), [])})" for c in t.columns
            )
            tables_desc.append(f"表 {t.name}: {cols}")
        prompt = (
            "你是数据字典专家。根据表结构与采样值，为每个列推断简短中文业务描述。\n"
            "只输出 JSON：{\"表名.列名\": \"描述\"}，不确定的列写 \"?\"。\n\n"
            + "\n".join(tables_desc)
        )
        try:
            data = _extract_json(await self._complete(prompt))
        except (json.JSONDecodeError, ValueError):
            return {}
        out = {}
        for key, desc in data.items():
            if "." in key and desc and desc != "?":
                table, column = key.split(".", 1)
                out[(table, column)] = str(desc)
        return out

    async def suggest_entity_names(
        self, clusters: list[EntityCluster]
    ) -> dict[int, str]:
        """实体簇命名："实体_1" → "客户"。"""
        if not clusters:
            return {}
        desc = "\n".join(
            f"{i}: 列 {', '.join(f'{t}.{c}' for t, c in cl.members)}"
            for i, cl in enumerate(clusters)
        )
        prompt = (
            "以下每组数据库列指向同一业务实体，为每组起一个简短中文实体名"
            "（如 客户/订单/商品）。只输出 JSON：{\"0\": \"名称\"}。\n\n" + desc
        )
        try:
            data = _extract_json(await self._complete(prompt))
            return {int(k): str(v) for k, v in data.items()}
        except (json.JSONDecodeError, ValueError):
            return {}

    async def draft_enum_questions(
        self, profiles: list[ColumnProfile], queue: ConfirmationQueue
    ) -> list[ConfirmationItem]:
        """枚举含义：LLM 猜测 → 生成选择题进确认队列（人判决，LLM 不判决）。"""
        enums = [p for p in profiles if p.is_enum]
        if not enums:
            return []
        desc = "\n".join(
            f"{p.table}.{p.column}: 值 {p.enum_values}" for p in enums[:20]
        )
        prompt = (
            "以下是枚举列及其取值，推测每列每个值的业务含义。只输出 JSON："
            "{\"表.列\": {\"值\": \"含义\"}}，无法推测的省略。\n\n" + desc
        )
        try:
            data = _extract_json(await self._complete(prompt))
        except (json.JSONDecodeError, ValueError):
            return []
        items = []
        for key, mapping in data.items():
            if "." not in key or not isinstance(mapping, dict):
                continue
            guessed = ", ".join(f"{v}={m}" for v, m in mapping.items())
            item = ConfirmationItem(
                item_id=__import__("uuid").uuid4().hex,
                kind="enum_meaning",
                question=f"枚举列 {key} 的取值含义是否为：{guessed}？",
                options=["正确", "不正确（需人工登记）"],
                context={"column": key, "mapping": mapping},
            )
            queue._items[item.item_id] = item  # noqa: SLF001 - 队列内部注册
            items.append(item)
        return items

    async def mine_documents(self, texts: list[str]) -> dict[str, dict]:
        """信号四：从 wiki/文档/IM 讨论中挖掘口径定义草稿。"""
        if not texts:
            return {}
        joined = "\n---\n".join(t[:2000] for t in texts[:10])
        prompt = (
            "从以下企业文档片段中提取数据指标口径定义。只输出 JSON："
            "{\"指标名\": {\"definition\": \"业务口径描述\", \"expr_hint\": \"计算提示\"}}。"
            "没有明确口径的不要编造。\n\n" + joined
        )
        try:
            data = _extract_json(await self._complete(prompt))
            return {
                str(k): {"definition": str(v.get("definition", "")),
                         "expr_hint": str(v.get("expr_hint", ""))}
                for k, v in data.items()
                if isinstance(v, dict)
            }
        except (json.JSONDecodeError, ValueError):
            return {}
