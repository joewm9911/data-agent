"""Schema 漂移监控（4.7 持续运转）：列改名/删除/类型突变 → 告警并冻结相关绑定。

冻结而非静默带病运行（铁律 P2）：受影响的实体绑定标记 frozen，
agent 上下文构建时跳过冻结绑定，直到人工确认新映射。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from da_types import CatalogSnapshot

from da_semantic.model import Entity
from da_semantic.store import SemanticStore

DriftKind = Literal["table_removed", "column_removed", "type_changed", "column_added"]


@dataclass
class DriftAlert:
    kind: DriftKind
    table: str
    column: str = ""
    detail: str = ""


@dataclass
class DriftReport:
    alerts: list[DriftAlert] = field(default_factory=list)
    frozen_bindings: list[str] = field(default_factory=list)  # "实体名: table.column"


def diff_catalogs(old: CatalogSnapshot, new: CatalogSnapshot) -> list[DriftAlert]:
    alerts: list[DriftAlert] = []
    old_tables = {t.name: t for t in old.tables}
    new_tables = {t.name: t for t in new.tables}

    for name, old_t in old_tables.items():
        new_t = new_tables.get(name)
        if new_t is None:
            alerts.append(DriftAlert(kind="table_removed", table=name))
            continue
        old_cols = {c.name: c.type for c in old_t.columns}
        new_cols = {c.name: c.type for c in new_t.columns}
        for col, typ in old_cols.items():
            if col not in new_cols:
                alerts.append(DriftAlert(kind="column_removed", table=name, column=col))
            elif new_cols[col] != typ:
                alerts.append(
                    DriftAlert(kind="type_changed", table=name, column=col,
                               detail=f"{typ} → {new_cols[col]}")
                )
        for col in new_cols:
            if col not in old_cols:
                alerts.append(DriftAlert(kind="column_added", table=name, column=col))
    return alerts


async def apply_drift_freeze(
    store: SemanticStore, alerts: list[DriftAlert], actor: str = "drift-monitor"
) -> DriftReport:
    """受漂移影响的实体绑定标记冻结（版本化写回，可追溯可回滚）。"""
    report = DriftReport(alerts=alerts)
    breaking = {
        (a.table, a.column) for a in alerts
        if a.kind in ("column_removed", "type_changed")
    }
    removed_tables = {a.table for a in alerts if a.kind == "table_removed"}
    if not breaking and not removed_tables:
        return report

    for name in await store.list_names("entity"):
        record = await store.get("entity", name)
        assert record is not None
        entity = Entity.model_validate(record.payload)
        payload = dict(record.payload)
        changed = False
        frozen = list(payload.get("frozen_bindings", []))
        for b in entity.bindings:
            ref = f"{b.table}.{b.column}"
            hit = (b.table, b.column) in breaking or b.table in removed_tables
            if hit and ref not in frozen:
                frozen.append(ref)
                report.frozen_bindings.append(f"{name}: {ref}")
                changed = True
        if changed:
            payload["frozen_bindings"] = frozen
            await store.put("entity", name, payload, actor)
    return report
