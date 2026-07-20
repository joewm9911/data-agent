"""主动层（架构文档 5.5）：监控 → 异常检测 → 自动触发归因 → 带诊断结论的简报。

推送的不是裸告警，而是已经带着思考的简报。异常检测用日序列 z-score（零依赖）。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from da_connectors.base import Connector
from da_types import GuardPolicy, Query, UserIdentity
from pydantic import BaseModel

from da_agent.metric_tree import AttributionReport, MetricNode, MetricTreeEngine
from da_agent.stats_guard import check_small_sample


class MonitorSpec(BaseModel):
    name: str
    metric: MetricNode
    # 日序列 SQL：输出 (day, value)，用于异常检测；含 {where}
    daily_sql: str
    z_threshold: float = 2.5
    # 异常触发归因时的基期/当期 where 模板（{day} 占位）
    base_where_tpl: str = "1=1"
    current_where_tpl: str = "1=1"


@dataclass
class Anomaly:
    day: str
    value: float
    mean: float
    std: float
    z: float


@dataclass
class Briefing:
    monitor: str
    anomalies: list[Anomaly] = field(default_factory=list)
    attribution: AttributionReport | None = None
    text: str = ""


class ProactiveMonitor:
    def __init__(self, connector: Connector, guard: GuardPolicy | None = None) -> None:
        self._connector = connector
        self._guard = guard or GuardPolicy()
        self._engine = MetricTreeEngine(connector, guard)

    async def _daily_series(
        self, sql: str, identity: UserIdentity
    ) -> list[tuple[str, float]]:
        result = await self._connector.execute(
            Query(statement=sql, dialect=self._connector.dialect), identity, self._guard
        )
        return [
            (str(r[0]), float(r[1]) if r[1] is not None else 0.0) for r in result.rows
        ]

    @staticmethod
    def detect_anomalies(
        series: list[tuple[str, float]], z_threshold: float
    ) -> list[Anomaly]:
        """对每个点用"其余点"的均值/方差算 z-score（留一法，避免尖峰自稀释）。"""
        if len(series) < 7:
            return []
        values = [v for _, v in series]
        anomalies = []
        for i, (day, v) in enumerate(series):
            rest = values[:i] + values[i + 1 :]
            mean = sum(rest) / len(rest)
            var = sum((x - mean) ** 2 for x in rest) / len(rest)
            std = math.sqrt(var)
            if std == 0:
                continue
            z = (v - mean) / std
            if abs(z) >= z_threshold:
                anomalies.append(Anomaly(day=day, value=v, mean=mean, std=std, z=z))
        return anomalies

    async def run(self, spec: MonitorSpec, identity: UserIdentity) -> Briefing:
        series = await self._daily_series(spec.daily_sql.format(where="1=1"), identity)
        anomalies = self.detect_anomalies(series, spec.z_threshold)
        briefing = Briefing(monitor=spec.name, anomalies=anomalies)

        if not anomalies:
            briefing.text = f"[{spec.name}] 最近 {len(series)} 天无异常。"
            return briefing

        peak = max(anomalies, key=lambda a: abs(a.z))
        attribution = await self._engine.attribute(
            spec.metric,
            base_where=spec.base_where_tpl.format(day=peak.day),
            current_where=spec.current_where_tpl.format(day=peak.day),
            identity=identity,
            base_label="异常前基线",
            current_label=f"异常日 {peak.day}",
        )
        small = check_small_sample(len(series), "监控序列")
        if small:
            attribution.warnings.append(small.message)
        briefing.attribution = attribution

        top = attribution.steps[0].top if attribution.steps else None
        driver = (
            f"主因：[{top.dimension}]中的「{top.member}」贡献 {top.share_of_total_delta:.0%}"
            if top
            else "驱动因素待人工排查"
        )
        briefing.text = (
            f"[{spec.name}] {peak.day} 异常（值 {peak.value:.0f}，"
            f"均值 {peak.mean:.0f}，z={peak.z:+.1f}）。{driver}。\n"
            f"{attribution.narrative()}"
        )
        return briefing
