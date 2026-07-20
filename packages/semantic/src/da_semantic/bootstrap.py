"""冷启动流水线编排（架构文档 4.2 五信号 → 语义层草稿 + 确认队列）。

第 1 天动作（12 章 SOP）：query_log 挖掘 + profiling → 证据图 → 自动合并实体草稿 +
中置信项进确认队列 + 指标草稿。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from da_connectors.base import Connector
from da_types import GuardPolicy, MetadataScope, TimeWindow, UserIdentity

from da_semantic.confirmation import ConfirmationQueue
from da_semantic.evidence import EvidenceGraph, UnificationResult
from da_semantic.mining import MiningReport, mine_query_log
from da_semantic.model import Binding, Entity, JoinPath, Metric
from da_semantic.profiling import ColumnProfile, profile_catalog, value_overlaps
from da_semantic.store import SemanticStore


@dataclass
class BootstrapReport:
    mining: MiningReport
    profiles: list[ColumnProfile] = field(default_factory=list)
    unification: UnificationResult | None = None
    entities_created: list[str] = field(default_factory=list)
    metrics_drafted: list[str] = field(default_factory=list)
    confirmations_queued: int = 0


async def bootstrap_semantic_layer(
    connector: Connector,
    store: SemanticStore,
    queue: ConfirmationQueue,
    identity: UserIdentity,
    window: TimeWindow,
    actor: str = "bootstrap",
) -> BootstrapReport:
    # 信号一：查询日志
    history = [hq async for hq in connector.get_query_history(window)]
    mining = mine_query_log(history, connector.dialect)

    # 信号三：schema profiling
    catalog = await connector.get_metadata(MetadataScope())
    profiles = await profile_catalog(
        connector, catalog, identity, GuardPolicy(max_result_rows=500)
    )

    # 证据图
    graph = EvidenceGraph()
    graph.add_join_evidence(mining.joins)
    graph.add_overlap_evidence(value_overlaps(profiles))
    graph.add_name_similarity(profiles)
    unification = graph.unify()

    report = BootstrapReport(mining=mining, profiles=profiles, unification=unification)
    heat = dict(mining.table_heat)

    # 高置信簇 → 实体草稿
    for idx, cluster in enumerate(unification.auto_clusters):
        name = f"实体_{idx + 1}"
        entity = Entity(
            name=name,
            canonical_key=cluster.members[0][1],
            bindings=[Binding(table=t, column=c) for t, c in cluster.members],
            join_paths=[
                JoinPath(
                    expr=f"{e.left[0]}.{e.left[1]} = {e.right[0]}.{e.right[1]}",
                    evidence="query_log" if e.kind == "query_log" else "value_overlap",
                    confidence=min(e.score, 1.0),
                )
                for e in cluster.evidences
                if e.kind in ("query_log", "value_overlap")
            ],
        )
        await store.put("entity", name, entity.model_dump(), actor)
        report.entities_created.append(name)

    # 中置信 → 确认队列（按涉及表的热度排序）
    for edge in unification.to_confirm:
        priority = heat.get(edge.left[0], 0) + heat.get(edge.right[0], 0)
        queue.add_entity_merge(edge, priority=priority)
        report.confirmations_queued += 1

    # 高频聚合 → 指标草稿（verified=False，待确认）
    for idx, (agg_sql, count) in enumerate(mining.frequent_aggregations[:10]):
        name = f"指标草稿_{idx + 1}"
        metric = Metric(
            name=name,
            definition=f"从查询日志挖掘（出现 {count} 次），待业务确认",
            expr=agg_sql,
            verified=False,
        )
        await store.put("metric", name, metric.model_dump(), actor)
        report.metrics_drafted.append(name)

    return report
