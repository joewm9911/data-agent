"""Eval 自动生成（8.3）：从查询日志抽真实问题建 golden set + 准确率周趋势。

golden 由历史 SQL 以系统身份重执行得到（独立于产品链路）；
问题文本可选用 LLM 生成自然中文问法（CompleteFn 注入），无 LLM 时用模板。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from da_connectors.base import Connector, ConnectorError
from da_types import GuardPolicy, HistoricalQuery, Query, UserIdentity

from da_evals.harness import EvalCase, EvalReport

CompleteFn = Callable[[str], Awaitable[str]]


@dataclass
class GeneratedCase:
    case: EvalCase
    source_sql: str


async def generate_eval_cases(
    history: list[HistoricalQuery],
    connector: Connector,
    identity: UserIdentity,
    llm: CompleteFn | None = None,
    max_cases: int = 20,
) -> list[GeneratedCase]:
    """挑选可作 golden 的历史查询（聚合、结果小而稳定），生成 eval 用例。"""
    cases: list[GeneratedCase] = []
    guard = GuardPolicy(max_result_rows=50)
    for hq in history:
        if len(cases) >= max_cases:
            break
        sql = hq.sql.strip()
        if not sql.lower().startswith(("select", "with")):
            continue
        try:
            result = await connector.execute(
                Query(statement=sql, dialect=connector.dialect), identity, guard
            )
        except ConnectorError:
            continue
        # golden 值：小结果集的数值单元格
        if not (0 < len(result.rows) <= 10):
            continue
        expected = [
            str(v) for row in result.rows for v in row
            if isinstance(v, int | float) and v != 0
        ][:3]
        if not expected:
            continue

        if llm is not None:
            try:
                question = (await llm(
                    "把这条 SQL 转写成业务人员会问的一句中文问题（只输出问题本身）：\n"
                    + sql
                )).strip().splitlines()[0]
            except Exception:  # noqa: BLE001 - LLM 失败退化为模板
                question = f"请计算并回答（口径参考：{sql[:100]}）"
        else:
            question = f"请计算并回答（口径参考：{sql[:100]}）"

        cases.append(
            GeneratedCase(
                case=EvalCase(
                    case_id=f"gen-{hq.query_id}",
                    question=question,
                    expected=expected,
                    any_of=True,  # 命中任一 golden 数值即通过
                    tags=["generated"],
                ),
                source_sql=sql,
            )
        )
    return cases


def trend_markdown(reports: list[EvalReport]) -> str:
    """准确率趋势（北极星指标：周环比持续为正，12 章）。"""
    if not reports:
        return "（尚无 eval 历史）"
    lines = ["# 准确率趋势", "", "| 运行时间 | 用例 | 准确率 | 环比 |", "|---|---|---|---|"]
    prev: float | None = None
    for r in sorted(reports, key=lambda x: x.ran_at):
        delta = "" if prev is None else f"{(r.accuracy - prev) * 100:+.0f}pp"
        lines.append(
            f"| {r.ran_at.strftime('%m-%d %H:%M')} | {r.total} "
            f"| {r.accuracy:.0%} | {delta} |"
        )
        prev = r.accuracy
    latest_delta = (
        reports[-1].accuracy - reports[-2].accuracy if len(reports) >= 2 else 0.0
    )
    verdict = "✅ 飞轮在转" if latest_delta >= 0 else "⚠ 准确率回退，禁止发布（8.3 门槛）"
    lines.append(f"\n{verdict}")
    return "\n".join(lines)
