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
    """指标树节点：指标 + 计算 SQL 模板 + 可分解维度 + 乘法子因子。"""

    name: str
    # SQL 模板，必须包含 {where} 占位（周期过滤注入点）；输出单行单列聚合值
    value_sql: str
    # 维度名 → 分组 SQL 模板（输出两列：维度值, 聚合值），同样含 {where}
    dimensions: dict[str, str] = Field(default_factory=dict)
    # 乘法分解子因子（如 GMV = 订单量 × 客单价）：各因子也是 MetricNode，
    # 语义约束：父值 ≈ Π(子因子值)。因子贡献用连乘替代法计算。
    factors: list[MetricNode] = Field(default_factory=list)
    # 维度下钻映射：维度名 → 该维度取值注入过滤的 SQL 片段模板（{member} 占位），
    # 供递归下钻在 top 分支上追加过滤条件
    drill_filters: dict[str, str] = Field(default_factory=dict)


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
class FactorContribution:
    """乘法因子贡献（连乘替代法：按序把因子从基期换成当期，增量归属该因子）。"""

    factor: str
    base_value: float
    current_value: float
    contribution: float  # 对父指标 delta 的绝对贡献
    share: float


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
    factor_steps: list[FactorContribution] = field(default_factory=list)
    # 递归下钻：top 分支的子报告（"华南跌了" → 下钻华南内部继续分解）
    drill_down: AttributionReport | None = None
    drill_member: str = ""
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
        for fc in self.factor_steps:
            lines.append(
                f"因子[{fc.factor}]: {fc.base_value:,.2f} → {fc.current_value:,.2f}"
                f"（贡献 Δ {fc.contribution:+,.2f}，占 {fc.share:.0%}）"
            )
        for step in self.steps:
            lines.append(f"按[{step.dimension}]分解：")
            for c in step.contributions[:6]:
                lines.append(
                    f"  - {c.member}: {c.base_value:,.2f} → {c.current_value:,.2f}"
                    f"（Δ {c.delta:+,.2f}，贡献 {c.share_of_total_delta:.0%}）"
                )
        if self.drill_down is not None:
            sub = self.drill_down.narrative().replace("\n", "\n    ")
            lines.append(f"↳ 下钻[{self.drill_member}]：\n    {sub}")
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
        drill_depth: int = 1,
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

        # 乘法因子分解（连乘替代法）：GMV = 订单量 × 客单价 …
        if node.factors:
            base_vals, curr_vals = [], []
            for f in node.factors:
                b_sql = f.value_sql.format(where=base_where)
                c_sql = f.value_sql.format(where=current_where)
                base_vals.append(await self._scalar(b_sql, identity))
                curr_vals.append(await self._scalar(c_sql, identity))
                report.evidence_sql.extend([b_sql, c_sql])
            running = list(base_vals)
            prev_product = _product(running)
            for i, f in enumerate(node.factors):
                running[i] = curr_vals[i]
                new_product = _product(running)
                contribution = new_product - prev_product
                prev_product = new_product
                report.factor_steps.append(
                    FactorContribution(
                        factor=f.name,
                        base_value=base_vals[i],
                        current_value=curr_vals[i],
                        contribution=contribution,
                        share=(contribution / delta) if delta else 0.0,
                    )
                )

        # 递归下钻：top 分支注入过滤后继续分解次级维度
        if drill_depth > 0 and report.steps:
            top_step = report.steps[0]
            top = top_step.top
            drill_tpl = node.drill_filters.get(top_step.dimension)
            if top is not None and drill_tpl is not None and len(node.dimensions) > 1:
                member_filter = drill_tpl.format(member=top.member.replace("'", "''"))
                sub_node = node.model_copy(
                    update={
                        "name": f"{node.name}[{top.member}]",
                        "dimensions": {
                            k: v for k, v in node.dimensions.items()
                            if k != top_step.dimension
                        },
                        "drill_filters": {
                            k: v for k, v in node.drill_filters.items()
                            if k != top_step.dimension
                        },
                        "factors": [],
                    }
                )
                report.drill_member = f"{top_step.dimension}={top.member}"
                report.drill_down = await self.attribute(
                    sub_node,
                    base_where=f"({base_where}) AND {member_filter}",
                    current_where=f"({current_where}) AND {member_filter}",
                    identity=identity,
                    base_label=base_label,
                    current_label=current_label,
                    max_dimensions=max_dimensions,
                    drill_depth=drill_depth - 1,
                )
        return report


def _product(values: list[float]) -> float:
    out = 1.0
    for v in values:
        out *= v
    return out


def draft_metric_trees(catalog, profiles) -> dict[str, MetricNode]:
    """指标树自动草稿（5.2 飞轮）：每表生成计数树，枚举列为维度并支持下钻。

    catalog: CatalogSnapshot；profiles: list[ColumnProfile]（枚举检测结果）。
    草稿供人工确认/LLM 命名后转正，不直接 verified。
    """
    enums_by_table: dict[str, list[str]] = {}
    for p in profiles:
        if p.is_enum:
            enums_by_table.setdefault(p.table, []).append(p.column)

    trees: dict[str, MetricNode] = {}
    for table in catalog.tables:
        enum_cols = enums_by_table.get(table.name, [])
        if not enum_cols:
            continue
        name = f"{table.name}量"
        trees[name] = MetricNode(
            name=name,
            value_sql=f"SELECT COUNT(*) FROM {table.name} WHERE {{where}}",
            dimensions={
                col: (
                    f"SELECT {col}, COUNT(*) FROM {table.name} "
                    f"WHERE {{where}} GROUP BY {col}"
                )
                for col in enum_cols[:4]
            },
            drill_filters={col: f"{col} = '{{member}}'" for col in enum_cols[:4]},
        )
    return trees
