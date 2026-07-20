"""指标树归因引擎（架构文档 5.2，产品心脏）。

"为什么跌了" = 树上的结构化搜索：逐维度分解 → 贡献度排序 → 最大分支下钻。
归因是确定性算法（每步可验证），LLM 只负责解读——铁律 P1 的落点。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from da_connectors.base import Connector
from da_types import GuardPolicy, Query, UserIdentity
from pydantic import BaseModel, Field


class MetricNode(BaseModel):
    """指标树节点：指标 + 计算 SQL 模板 + 可分解维度。"""

    name: str
    # SQL 模板，必须包含 {where} 占位（周期过滤注入点）；输出单行单列聚合值
    value_sql: str
    # 维度名 → 分组 SQL 模板（输出两列：维度值, 聚合值），同样含 {where}
    dimensions: dict[str, str] = Field(default_factory=dict)
    # 子指标（如 GMV = 流量×转化率×客单价 的分解），M2 先支持维度分解
    children: list[MetricNode] = Field(default_factory=list)


@dataclass
class Contribution:
    dimension: str
    member: str
    base_value: float
    current_value: float
    delta: float
    share_of_total_delta: float  # 对总变化的贡献占比


@dataclass
class AttributionStep:
    dimension: str
    contributions: list[Contribution]
    top: Contribution | None


@dataclass
class AttributionReport:
    metric: str
    base_period: str
    current_period: str
    base_total: float
    current_total: float
    delta: float
    delta_pct: float | None
    steps: list[AttributionStep] = field(default_factory=list)
    evidence_sql: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def narrative(self) -> str:
        """带证据链的结构化叙述（供 LLM 解读或直接呈现）。"""
        pct = f"{self.delta_pct:+.1%}" if self.delta_pct is not None else "n/a"
        lines = [
            f"指标[{self.metric}] {self.base_period} → {self.current_period}："
            f"{self.base_total:,.2f} → {self.current_total:,.2f}"
            f"（Δ {self.delta:+,.2f}，{pct}）"
        ]
        for step in self.steps:
            lines.append(f"按[{step.dimension}]分解：")
            for c in step.contributions[:6]:
                lines.append(
                    f"  - {c.member}: {c.base_value:,.2f} → {c.current_value:,.2f}"
                    f"（Δ {c.delta:+,.2f}，贡献 {c.share_of_total_delta:.0%}）"
                )
        lines.extend(f"⚠ {w}" for w in self.warnings)
        return "\n".join(lines)


class MetricTreeEngine:
    def __init__(
        self,
        connector: Connector,
        guard: GuardPolicy | None = None,
    ) -> None:
        self._connector = connector
        self._guard = guard or GuardPolicy()

    async def _scalar(self, sql: str, identity: UserIdentity) -> float:
        result = await self._connector.execute(
            Query(statement=sql, dialect=self._connector.dialect), identity, self._guard
        )
        if not result.rows or result.rows[0][0] is None:
            return 0.0
        return float(result.rows[0][0])

    async def _grouped(self, sql: str, identity: UserIdentity) -> dict[str, float]:
        result = await self._connector.execute(
            Query(statement=sql, dialect=self._connector.dialect), identity, self._guard
        )
        return {
            str(row[0]): float(row[1]) if row[1] is not None else 0.0
            for row in result.rows
        }

    async def attribute(
        self,
        node: MetricNode,
        base_where: str,
        current_where: str,
        identity: UserIdentity,
        base_label: str = "基期",
        current_label: str = "当期",
        max_dimensions: int = 3,
    ) -> AttributionReport:
        base_sql = node.value_sql.format(where=base_where)
        curr_sql = node.value_sql.format(where=current_where)
        base_total = await self._scalar(base_sql, identity)
        current_total = await self._scalar(curr_sql, identity)
        delta = current_total - base_total

        report = AttributionReport(
            metric=node.name,
            base_period=base_label,
            current_period=current_label,
            base_total=base_total,
            current_total=current_total,
            delta=delta,
            delta_pct=(delta / base_total) if base_total else None,
            evidence_sql=[base_sql, curr_sql],
        )

        for dim_name, dim_sql in list(node.dimensions.items())[:max_dimensions]:
            b_sql = dim_sql.format(where=base_where)
            c_sql = dim_sql.format(where=current_where)
            base_groups = await self._grouped(b_sql, identity)
            curr_groups = await self._grouped(c_sql, identity)
            report.evidence_sql.extend([b_sql, c_sql])

            contributions = []
            for member in sorted(set(base_groups) | set(curr_groups)):
                b = base_groups.get(member, 0.0)
                c = curr_groups.get(member, 0.0)
                d = c - b
                contributions.append(
                    Contribution(
                        dimension=dim_name,
                        member=member,
                        base_value=b,
                        current_value=c,
                        delta=d,
                        share_of_total_delta=(d / delta) if delta else 0.0,
                    )
                )
            # 按对总变化的绝对贡献排序（涨跌同向排序）
            contributions.sort(key=lambda x: -abs(x.delta))
            report.steps.append(
                AttributionStep(
                    dimension=dim_name,
                    contributions=contributions,
                    top=contributions[0] if contributions else None,
                )
            )
        return report
