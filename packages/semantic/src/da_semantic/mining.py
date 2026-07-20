"""查询日志挖掘（架构文档 4.2 信号一，信息密度最高）。

从历史 SQL 中提取：
- join 等值条件（实体归一的判决性证据）
- 高频过滤惯例（隐性口径，如"排除测试账号"）
- 聚合表达式（指标草稿）
- 表访问热度（确认队列幂律排序依据）
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

import sqlglot
from da_types import HistoricalQuery
from sqlglot import expressions as exp


@dataclass
class JoinEvidence:
    left: tuple[str, str]  # (table, column)
    right: tuple[str, str]
    count: int = 0


@dataclass
class MiningReport:
    total_queries: int = 0
    parsed_queries: int = 0
    joins: list[JoinEvidence] = field(default_factory=list)
    frequent_filters: list[tuple[str, int]] = field(default_factory=list)
    frequent_aggregations: list[tuple[str, int]] = field(default_factory=list)
    table_heat: list[tuple[str, int]] = field(default_factory=list)


def mine_query_log(
    history: list[HistoricalQuery],
    dialect: str,
    min_join_count: int = 2,
    top_n: int = 20,
) -> MiningReport:
    report = MiningReport(total_queries=len(history))
    join_counter: Counter[tuple[tuple[str, str], tuple[str, str]]] = Counter()
    filter_counter: Counter[str] = Counter()
    agg_counter: Counter[str] = Counter()
    table_counter: Counter[str] = Counter()

    for hq in history:
        try:
            tree = sqlglot.parse_one(hq.sql, read=dialect)
        except sqlglot.errors.ParseError:
            continue
        report.parsed_queries += 1

        alias_map: dict[str, str] = {}
        for t in tree.find_all(exp.Table):
            table_counter[t.name] += 1
            if t.alias:
                alias_map[t.alias] = t.name

        # join 等值条件：显式 JOIN ON 与 WHERE 中的跨表等值都算
        for eq in tree.find_all(exp.EQ):
            left, right = eq.this, eq.expression
            if isinstance(left, exp.Column) and isinstance(right, exp.Column):
                lc = _resolve_column(left, alias_map)
                rc = _resolve_column(right, alias_map)
                if lc and rc and lc[0] != rc[0]:
                    key = tuple(sorted([lc, rc]))
                    join_counter[key] += 1  # type: ignore[index]

        # WHERE 过滤惯例（跳过纯日期范围——那是查询参数不是口径）
        where = tree.args.get("where")
        if where is not None:
            for cond in _leaf_conditions(where.this):
                if not _looks_like_date_filter(cond):
                    filter_counter[cond.sql(dialect=dialect)] += 1

        for func in tree.find_all(exp.AggFunc):
            agg_counter[func.sql(dialect=dialect)] += 1

    report.joins = [
        JoinEvidence(left=k[0], right=k[1], count=c)
        for k, c in join_counter.most_common()
        if c >= min_join_count
    ]
    report.frequent_filters = filter_counter.most_common(top_n)
    report.frequent_aggregations = agg_counter.most_common(top_n)
    report.table_heat = table_counter.most_common()
    return report


def _resolve_column(col: exp.Column, alias_map: dict[str, str]) -> tuple[str, str] | None:
    table_ref = col.table
    if not table_ref:
        return None
    return alias_map.get(table_ref, table_ref), col.name


def _leaf_conditions(node: exp.Expression) -> list[exp.Expression]:
    if isinstance(node, exp.And):
        return _leaf_conditions(node.this) + _leaf_conditions(node.expression)
    return [node]


def _looks_like_date_filter(cond: exp.Expression) -> bool:
    if isinstance(cond, exp.Between):
        return True
    for lit in cond.find_all(exp.Literal):
        if lit.is_string and len(lit.this) >= 8 and lit.this[:4].isdigit():
            return True
    return False
