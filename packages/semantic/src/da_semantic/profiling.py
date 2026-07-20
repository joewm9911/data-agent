"""Schema profiling（架构文档 4.2 信号三）：采样值分布，产出实体归一的值域证据。

- 枚举检测：低基数列 + 值清单（EnumMapping 草稿素材）
- 值域重叠：两列采样值的包含度（containment）——列名毫无相似度时的判决证据
"""

from __future__ import annotations

from dataclasses import dataclass, field

from da_connectors.base import Connector
from da_types import CatalogSnapshot, GuardPolicy, Query, UserIdentity

PROFILE_SAMPLE_ROWS = 500
ENUM_MAX_CARDINALITY = 24


@dataclass
class ColumnProfile:
    table: str
    column: str
    sampled: int = 0
    nulls: int = 0
    distinct: int = 0
    is_enum: bool = False
    enum_values: list[str] = field(default_factory=list)
    samples: set[str] = field(default_factory=set)


@dataclass
class OverlapEvidence:
    left: tuple[str, str]
    right: tuple[str, str]
    containment: float  # min 侧包含度


async def profile_catalog(
    connector: Connector,
    catalog: CatalogSnapshot,
    identity: UserIdentity,
    guard: GuardPolicy | None = None,
) -> list[ColumnProfile]:
    guard = guard or GuardPolicy(max_result_rows=PROFILE_SAMPLE_ROWS)
    profiles: list[ColumnProfile] = []
    for table in catalog.tables:
        for col in table.columns:
            result = await connector.execute(
                Query(
                    statement=(
                        f"SELECT {col.name} FROM {table.name} LIMIT {PROFILE_SAMPLE_ROWS}"
                    ),
                    dialect=connector.dialect,
                ),
                identity,
                guard,
            )
            values = [row[0] for row in result.rows]
            non_null = [v for v in values if v is not None]
            distinct = {str(v) for v in non_null}
            profile = ColumnProfile(
                table=table.name,
                column=col.name,
                sampled=len(values),
                nulls=len(values) - len(non_null),
                distinct=len(distinct),
                samples=distinct,
            )
            if 0 < len(distinct) <= ENUM_MAX_CARDINALITY and len(non_null) >= 20:
                profile.is_enum = True
                profile.enum_values = sorted(distinct)
            profiles.append(profile)
    return profiles


def value_overlaps(
    profiles: list[ColumnProfile],
    min_containment: float = 0.8,
    min_distinct: int = 20,
) -> list[OverlapEvidence]:
    """两两比较高基数列的采样值包含度。枚举/低基数列跳过（重叠无意义）。"""
    candidates = [p for p in profiles if not p.is_enum and p.distinct >= min_distinct]
    evidences = []
    for i, a in enumerate(candidates):
        for b in candidates[i + 1 :]:
            if a.table == b.table:
                continue
            denom = min(len(a.samples), len(b.samples))
            if denom == 0:
                continue
            containment = len(a.samples & b.samples) / denom
            if containment >= min_containment:
                evidences.append(
                    OverlapEvidence(
                        left=(a.table, a.column),
                        right=(b.table, b.column),
                        containment=round(containment, 3),
                    )
                )
    return evidences
