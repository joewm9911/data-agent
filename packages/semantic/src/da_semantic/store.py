"""语义层版本化存储（M0 不可后补项：从第一天起全量版本化）。

每次 put 版本号 +1 并保留完整历史——纠正历史/确认记录是带不走的护城河资产。
生产实现为 Postgres；内存实现用于测试与单机模式，语义一致。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal, Protocol

from pydantic import BaseModel

ObjectKind = Literal["entity", "metric", "counter_example", "verified_answer"]


class VersionedRecord(BaseModel):
    kind: ObjectKind
    name: str
    version: int
    payload: dict
    updated_by: str
    updated_at: datetime


class SemanticStore(Protocol):
    async def put(
        self, kind: ObjectKind, name: str, payload: dict, actor: str
    ) -> VersionedRecord: ...

    async def get(self, kind: ObjectKind, name: str) -> VersionedRecord | None:
        """返回最新版本。"""
        ...

    async def history(self, kind: ObjectKind, name: str) -> list[VersionedRecord]: ...

    async def list_names(self, kind: ObjectKind) -> list[str]: ...


class InMemorySemanticStore:
    def __init__(self) -> None:
        self._records: dict[tuple[str, str], list[VersionedRecord]] = {}

    async def put(
        self, kind: ObjectKind, name: str, payload: dict, actor: str
    ) -> VersionedRecord:
        key = (kind, name)
        versions = self._records.setdefault(key, [])
        record = VersionedRecord(
            kind=kind,
            name=name,
            version=len(versions) + 1,
            payload=payload,
            updated_by=actor,
            updated_at=datetime.now(UTC),
        )
        versions.append(record)
        return record

    async def get(self, kind: ObjectKind, name: str) -> VersionedRecord | None:
        versions = self._records.get((kind, name))
        return versions[-1] if versions else None

    async def history(self, kind: ObjectKind, name: str) -> list[VersionedRecord]:
        return list(self._records.get((kind, name), []))

    async def list_names(self, kind: ObjectKind) -> list[str]:
        return sorted(n for k, n in self._records if k == kind)
