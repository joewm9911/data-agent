"""Playbook 方法论库（架构文档 5.3）：分析师套路的产品化，垂直深度的沉淀层。

Playbook = 声明式步骤（SQL 模板）+ 解读框架；引擎按套路确定性执行，输出质量稳定。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from da_connectors.base import Connector
from da_types import GuardPolicy, Query, UserIdentity
from pydantic import BaseModel, Field


class PlaybookStep(BaseModel):
    title: str
    sql: str  # 可含 {param} 占位
    interpret_hint: str = ""


class PlaybookSpec(BaseModel):
    name: str
    description: str
    trigger_keywords: list[str] = Field(default_factory=list)
    params: list[str] = Field(default_factory=list)
    steps: list[PlaybookStep] = Field(default_factory=list)


@dataclass
class StepResult:
    title: str
    sql: str
    columns: list[str]
    rows: list[list]
    interpret_hint: str = ""


@dataclass
class PlaybookRun:
    playbook: str
    results: list[StepResult] = field(default_factory=list)

    def narrative(self) -> str:
        lines = [f"Playbook[{self.playbook}] 执行结果："]
        for r in self.results:
            lines.append(f"## {r.title}")
            lines.append(",".join(r.columns))
            for row in r.rows[:10]:
                lines.append(",".join(str(v) for v in row))
            if r.interpret_hint:
                lines.append(f"（解读要点：{r.interpret_hint}）")
        return "\n".join(lines)


class PlaybookRegistry:
    def __init__(self) -> None:
        self._specs: dict[str, PlaybookSpec] = {}

    def register(self, spec: PlaybookSpec) -> None:
        self._specs[spec.name] = spec

    def get(self, name: str) -> PlaybookSpec:
        return self._specs[name]

    def names(self) -> list[str]:
        return sorted(self._specs)

    def match(self, question: str) -> PlaybookSpec | None:
        best, best_hits = None, 0
        for spec in self._specs.values():
            hits = sum(1 for kw in spec.trigger_keywords if kw in question)
            if hits > best_hits:
                best, best_hits = spec, hits
        return best


class PlaybookEngine:
    def __init__(self, connector: Connector, guard: GuardPolicy | None = None) -> None:
        self._connector = connector
        self._guard = guard or GuardPolicy()

    async def run(
        self, spec: PlaybookSpec, params: dict[str, str], identity: UserIdentity
    ) -> PlaybookRun:
        missing = [p for p in spec.params if p not in params]
        if missing:
            raise ValueError(f"playbook 缺少参数: {missing}")
        run = PlaybookRun(playbook=spec.name)
        for step in spec.steps:
            sql = step.sql.format(**params)
            result = await self._connector.execute(
                Query(statement=sql, dialect=self._connector.dialect),
                identity,
                self._guard,
            )
            run.results.append(
                StepResult(
                    title=step.title,
                    sql=sql,
                    columns=[c.name for c in result.columns],
                    rows=result.rows,
                    interpret_hint=step.interpret_hint,
                )
            )
        return run


def cx_ticket_anomaly_playbook() -> PlaybookSpec:
    """CX 垂直包：工单量异常诊断（5.3）。"""
    return PlaybookSpec(
        name="工单量异常诊断",
        description="对比两个周期的工单量，按类型/解决率分解，定位异常驱动",
        trigger_keywords=["工单", "异常", "上涨", "激增", "变多"],
        params=["base_start", "base_end", "curr_start", "curr_end"],
        steps=[
            PlaybookStep(
                title="两期总量对比",
                sql=(
                    "SELECT CASE WHEN created_at BETWEEN '{base_start}' AND '{base_end}' "
                    "THEN '基期' ELSE '当期' END AS period, COUNT(*) AS tickets "
                    "FROM cs_tickets WHERE created_at BETWEEN '{base_start}' AND '{curr_end}' "
                    "GROUP BY period"
                ),
                interpret_hint="总量变化幅度",
            ),
            PlaybookStep(
                title="按类型分解（当期）",
                sql=(
                    "SELECT cat, COUNT(*) AS n FROM cs_tickets "
                    "WHERE created_at BETWEEN '{curr_start}' AND '{curr_end}' "
                    "GROUP BY cat ORDER BY n DESC"
                ),
                interpret_hint="哪类工单驱动变化",
            ),
            PlaybookStep(
                title="未解决工单比例（当期）",
                sql=(
                    "SELECT resolved, COUNT(*) AS n FROM cs_tickets "
                    "WHERE created_at BETWEEN '{curr_start}' AND '{curr_end}' "
                    "GROUP BY resolved"
                ),
                interpret_hint="积压风险",
            ),
        ],
    )


def channel_review_playbook() -> PlaybookSpec:
    """通用包：渠道复盘。"""
    return PlaybookSpec(
        name="渠道复盘",
        description="按渠道拆解 GMV 与订单量，评估结构变化",
        trigger_keywords=["渠道", "复盘", "拆解", "结构"],
        params=["start", "end"],
        steps=[
            PlaybookStep(
                title="渠道 GMV 与订单量",
                sql=(
                    "SELECT chan, ROUND(SUM(order_amt), 2) AS gmv, COUNT(*) AS orders "
                    "FROM orders WHERE stat = 1 AND cust_no NOT LIKE 'TEST%' "
                    "AND pay_dt BETWEEN '{start}' AND '{end}' "
                    "GROUP BY chan ORDER BY gmv DESC"
                ),
                interpret_hint="渠道集中度与头部贡献",
            ),
        ],
    )
