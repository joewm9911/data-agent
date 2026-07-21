"""指标 SQL 组装、时间口径一致性校验与试算（设计稿定稿的执行语义）。

生成 SQL 形如：SELECT AGG(分子) FROM 分子表 WHERE 分子filter AND 时间范围；
分母同理。时间列由 time_field（语义角色）在各组件表上解析——跨表时两表都必须有绑定，
缺失即校验失败（宁可拒存，不给错口径）。
"""

from __future__ import annotations

from dataclasses import dataclass

from da_connectors.base import Connector
from da_types import GuardPolicy, Query, UserIdentity

from da_semantic.model import Entity, Metric, MetricComponent


def resolve_time_column(
    entities: list[Entity], role_name: str, table: str
) -> str | None:
    """语义角色名 + 表 → 物理列。返回 None 表示该表未绑定此角色。"""
    for entity in entities:
        for role in entity.semantic_roles:
            if role.role == role_name and role.table == table:
                return role.column
    return None


def validate_metric(metric: Metric, entities: list[Entity]) -> list[str]:
    """保存/试算前校验。返回错误列表（空 = 通过）。"""
    errors: list[str] = []
    if metric.numerator is None:
        if not metric.expr:
            errors.append("需要提供分子（或旧式 expr）")
        return errors

    if not metric.numerator.expr.strip():
        errors.append("分子表达式不能为空")
    if not metric.numerator.table.strip():
        errors.append("分子必须指定数据表")
    if metric.denominator is not None:
        if not metric.denominator.expr.strip():
            errors.append("分母表达式不能为空")
        if not metric.denominator.table.strip():
            errors.append("分母必须指定数据表")

    # 时间口径一致性（硬约束）：time_field 必须在每个组件表上都有绑定
    if metric.time_field:
        tables = {metric.numerator.table}
        if metric.denominator is not None and metric.denominator.table:
            tables.add(metric.denominator.table)
        for table in sorted(t for t in tables if t):
            if resolve_time_column(entities, metric.time_field, table) is None:
                errors.append(
                    f"请先在映射矩阵为表 {table} 绑定语义角色 {metric.time_field}"
                )
    return errors


def component_sql(
    component: MetricComponent,
    time_column: str | None,
    start: str = "",
    end: str = "",
) -> str:
    conditions = ["1=1"]
    if component.filter.strip():
        conditions.append(f"({component.filter})")
    if time_column and start and end:
        conditions.append(f"{time_column} BETWEEN '{start}' AND '{end}'")
    return (
        f"SELECT {component.expr} FROM {component.table} "
        f"WHERE {' AND '.join(conditions)}"
    )


@dataclass
class TrialResult:
    numerator_sql: str
    numerator_value: float | None
    denominator_sql: str = ""
    denominator_value: float | None = None
    ratio: float | None = None


async def trial_metric(
    connector: Connector,
    metric: Metric,
    entities: list[Entity],
    identity: UserIdentity,
    start: str,
    end: str,
    guard: GuardPolicy | None = None,
) -> TrialResult:
    """试算：真实执行分子/分母 SQL（经护栏），返回值与比率。校验失败抛 ValueError。"""
    errors = validate_metric(metric, entities)
    if errors:
        raise ValueError("；".join(errors))
    assert metric.numerator is not None
    guard = guard or GuardPolicy(max_result_rows=10)

    async def scalar(sql: str) -> float | None:
        result = await connector.execute(
            Query(statement=sql, dialect=connector.dialect), identity, guard
        )
        if not result.rows or result.rows[0][0] is None:
            return None
        return float(result.rows[0][0])

    num_time = (
        resolve_time_column(entities, metric.time_field, metric.numerator.table)
        if metric.time_field else None
    )
    num_sql = component_sql(metric.numerator, num_time, start, end)
    trial = TrialResult(numerator_sql=num_sql, numerator_value=await scalar(num_sql))

    if metric.denominator is not None:
        den_time = (
            resolve_time_column(entities, metric.time_field, metric.denominator.table)
            if metric.time_field else None
        )
        trial.denominator_sql = component_sql(metric.denominator, den_time, start, end)
        trial.denominator_value = await scalar(trial.denominator_sql)
        if trial.numerator_value is not None and trial.denominator_value:
            trial.ratio = trial.numerator_value / trial.denominator_value
    return trial
