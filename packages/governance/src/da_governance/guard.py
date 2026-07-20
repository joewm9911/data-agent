"""查询护栏（架构文档 3.4）。

铁律 P3 的落点之一：护栏在语句进入数据库之前强制执行，agent/上层无法绕过。
职责：只读校验、单语句校验、LIMIT 注入与钳制。代价预估（EXPLAIN/扫描量）由适配器补充。
"""

from __future__ import annotations

import sqlglot
from da_types import GuardDecision, GuardPolicy
from sqlglot import expressions as exp


def prepare_statement(statement: str, dialect: str, policy: GuardPolicy) -> GuardDecision:
    """校验并改写语句。返回的 rewritten_statement 是唯一允许执行的版本。"""
    try:
        parsed = sqlglot.parse(statement, read=dialect)
    except sqlglot.errors.ParseError as e:
        return GuardDecision(allowed=False, reason=f"SQL 解析失败: {e}")

    parsed = [p for p in parsed if p is not None]
    if len(parsed) != 1:
        return GuardDecision(allowed=False, reason=f"仅允许单条语句，收到 {len(parsed)} 条")

    tree = parsed[0]

    if policy.read_only and not _is_read_only(tree):
        return GuardDecision(allowed=False, reason=f"只读模式拒绝 {type(tree).__name__} 语句")

    if policy.force_limit:
        tree = _clamp_limit(tree, policy.max_result_rows)

    return GuardDecision(
        allowed=True,
        rewritten_statement=tree.sql(dialect=dialect),
    )


def referenced_objects(
    statement: str, dialect: str, default_database: str = "main"
) -> list[tuple[str, str]]:
    """提取语句引用的 (database, table) 列表，供执行前权限判定（6.1 第一/二层的执行点）。

    解析失败返回空列表——调用方应让护栏的解析错误路径先行拒绝。
    """
    try:
        tree = sqlglot.parse_one(statement, read=dialect)
    except sqlglot.errors.ParseError:
        return []
    cte_names = {cte.alias_or_name for cte in tree.find_all(exp.CTE)}
    objects = []
    for table in tree.find_all(exp.Table):
        if table.name in cte_names:
            continue
        objects.append((table.db or default_database, table.name))
    return sorted(set(objects))


def _is_read_only(tree: exp.Expression) -> bool:
    """SELECT（含 CTE/UNION）为只读；DDL/DML/SET 等一律拒绝。"""
    if isinstance(tree, (exp.Select, exp.Union)):
        return True
    # WITH ... SELECT
    if isinstance(tree, exp.With):
        return _is_read_only(tree.this)
    return False


def _clamp_limit(tree: exp.Expression, max_rows: int) -> exp.Expression:
    """无 LIMIT 则注入；LIMIT 大于上限则钳制到上限。作用于最外层查询。"""
    query = tree
    if isinstance(query, exp.With):
        query = query.this

    if not isinstance(query, (exp.Select, exp.Union)):
        return tree

    existing = query.args.get("limit")
    if existing is not None:
        try:
            current = int(existing.expression.this)
        except (TypeError, ValueError):
            current = None
        if current is not None and current <= max_rows:
            return tree
    query.set("limit", exp.Limit(expression=exp.Literal.number(max_rows)))
    return tree
