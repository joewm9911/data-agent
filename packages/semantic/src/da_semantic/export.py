"""开放格式导出（架构文档 4.6）："不锁定"降低采用门槛；带不走的是飞轮，不是格式。

导出为 dbt semantic layer 风格的 dict（可直接 dump 为 YAML）。
"""

from __future__ import annotations

from typing import Any

from da_semantic.model import Entity, Metric
from da_semantic.store import SemanticStore


async def export_semantic_layer(store: SemanticStore) -> dict[str, Any]:
    entities = []
    for name in await store.list_names("entity"):
        record = await store.get("entity", name)
        assert record is not None
        e = Entity.model_validate(record.payload)
        entities.append(
            {
                "name": e.name,
                "canonical_key": e.canonical_key,
                "aliases": e.aliases,
                "bindings": [b.model_dump() for b in e.bindings],
                "joins": [{"sql_on": j.expr, "confidence": j.confidence} for j in e.join_paths],
                "version": record.version,
            }
        )

    metrics = []
    for name in await store.list_names("metric"):
        record = await store.get("metric", name)
        assert record is not None
        m = Metric.model_validate(record.payload)
        metrics.append(
            {
                "name": m.name,
                "description": m.definition,
                "expr": m.expr,
                "dimensions": m.grain,
                "meta": {"verified": m.verified, "restricted": m.restricted,
                         "version": record.version},
            }
        )

    return {"semantic_layer": {"entities": entities, "metrics": metrics}}
