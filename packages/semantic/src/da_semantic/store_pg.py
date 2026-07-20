"""Postgres 语义层存储（4.1 版本化 + 持久化）。DDL 在源码中，幂等建表。

append-only：每次 put 插入新版本行，最新版本 = MAX(version)。历史永不删除（护城河资产）。
"""

from __future__ import annotations

import json

from da_platform.postgres import connect, ensure_schema

from da_semantic.store import ObjectKind, VersionedRecord

DDL = """
CREATE TABLE IF NOT EXISTS semantic_objects (
    id          BIGSERIAL PRIMARY KEY,
    tenant_id   TEXT NOT NULL DEFAULT 'default',
    kind        TEXT NOT NULL,
    name        TEXT NOT NULL,
    version     INTEGER NOT NULL,
    payload     JSONB NOT NULL,
    updated_by  TEXT NOT NULL,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, kind, name, version)
);
CREATE INDEX IF NOT EXISTS idx_semantic_latest
    ON semantic_objects (tenant_id, kind, name, version DESC);
"""


class PgSemanticStore:
    def __init__(self, dsn: str | None = None, tenant_id: str = "default") -> None:
        self._dsn = dsn
        self._tenant = tenant_id
        self._ready = False

    async def _conn(self):
        conn = await connect(self._dsn)
        if not self._ready:
            await ensure_schema(conn, DDL)
            self._ready = True
        return conn

    async def put(
        self, kind: ObjectKind, name: str, payload: dict, actor: str
    ) -> VersionedRecord:
        conn = await self._conn()
        try:
            cur = await conn.execute(
                """
                INSERT INTO semantic_objects (tenant_id, kind, name, version, payload, updated_by)
                VALUES (
                    %s, %s, %s,
                    COALESCE((SELECT MAX(version) FROM semantic_objects
                              WHERE tenant_id = %s AND kind = %s AND name = %s), 0) + 1,
                    %s::jsonb, %s
                )
                RETURNING version, updated_at
                """,
                (self._tenant, kind, name, self._tenant, kind, name,
                 json.dumps(payload, ensure_ascii=False), actor),
            )
            version, updated_at = await cur.fetchone()
            await conn.commit()
            return VersionedRecord(
                kind=kind, name=name, version=version, payload=payload,
                updated_by=actor, updated_at=updated_at,
            )
        finally:
            await conn.close()

    async def get(self, kind: ObjectKind, name: str) -> VersionedRecord | None:
        conn = await self._conn()
        try:
            cur = await conn.execute(
                """
                SELECT version, payload, updated_by, updated_at FROM semantic_objects
                WHERE tenant_id = %s AND kind = %s AND name = %s
                ORDER BY version DESC LIMIT 1
                """,
                (self._tenant, kind, name),
            )
            row = await cur.fetchone()
            if row is None:
                return None
            version, payload, updated_by, updated_at = row
            return VersionedRecord(
                kind=kind, name=name, version=version, payload=payload,
                updated_by=updated_by, updated_at=updated_at,
            )
        finally:
            await conn.close()

    async def history(self, kind: ObjectKind, name: str) -> list[VersionedRecord]:
        conn = await self._conn()
        try:
            cur = await conn.execute(
                """
                SELECT version, payload, updated_by, updated_at FROM semantic_objects
                WHERE tenant_id = %s AND kind = %s AND name = %s ORDER BY version
                """,
                (self._tenant, kind, name),
            )
            return [
                VersionedRecord(kind=kind, name=name, version=v, payload=p,
                                updated_by=by, updated_at=at)
                for v, p, by, at in await cur.fetchall()
            ]
        finally:
            await conn.close()

    async def list_names(self, kind: ObjectKind) -> list[str]:
        conn = await self._conn()
        try:
            cur = await conn.execute(
                "SELECT DISTINCT name FROM semantic_objects "
                "WHERE tenant_id = %s AND kind = %s ORDER BY name",
                (self._tenant, kind),
            )
            return [r[0] for r in await cur.fetchall()]
        finally:
            await conn.close()
