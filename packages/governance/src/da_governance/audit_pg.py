"""Postgres 审计存储（8.1）：不可变 insert-only 表，DDL 在源码。

不可变性由数据库层强制：REVOKE UPDATE/DELETE 需 DBA 在生产执行；
应用层本类只提供 append 与只读查询，无更新/删除路径。
"""

from __future__ import annotations

import json

from da_platform.postgres import connect, ensure_schema

from da_governance.audit import AuditEvent

DDL = """
CREATE TABLE IF NOT EXISTS audit_events (
    id          BIGSERIAL PRIMARY KEY,
    event_id    TEXT NOT NULL UNIQUE,
    ts          TIMESTAMPTZ NOT NULL,
    tenant_id   TEXT NOT NULL,
    session_id  TEXT NOT NULL,
    turn_id     TEXT NOT NULL,
    stage       TEXT NOT NULL,
    user_id     TEXT NOT NULL,
    identity    JSONB NOT NULL,
    payload     JSONB NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_audit_session ON audit_events (tenant_id, session_id, ts);
CREATE INDEX IF NOT EXISTS idx_audit_user ON audit_events (tenant_id, user_id, ts);
"""


class PgAuditSink:
    def __init__(self, dsn: str | None = None) -> None:
        self._dsn = dsn
        self._ready = False

    async def _conn(self):
        conn = await connect(self._dsn)
        if not self._ready:
            await ensure_schema(conn, DDL)
            self._ready = True
        return conn

    async def append(self, event: AuditEvent) -> None:
        conn = await self._conn()
        try:
            await conn.execute(
                """
                INSERT INTO audit_events
                    (event_id, ts, tenant_id, session_id, turn_id, stage, user_id,
                     identity, payload)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb)
                ON CONFLICT (event_id) DO NOTHING
                """,
                (event.event_id, event.ts, event.tenant_id, event.session_id,
                 event.turn_id, event.stage, event.identity.user_id,
                 event.identity.model_dump_json(),
                 json.dumps(event.payload, ensure_ascii=False, default=str)),
            )
            await conn.commit()
        finally:
            await conn.close()

    async def recent(
        self, tenant_id: str, limit: int = 100, session_id: str | None = None
    ) -> list[dict]:
        conn = await self._conn()
        try:
            if session_id:
                cur = await conn.execute(
                    "SELECT event_id, ts, session_id, turn_id, stage, user_id, payload "
                    "FROM audit_events WHERE tenant_id = %s AND session_id = %s "
                    "ORDER BY ts DESC LIMIT %s",
                    (tenant_id, session_id, limit),
                )
            else:
                cur = await conn.execute(
                    "SELECT event_id, ts, session_id, turn_id, stage, user_id, payload "
                    "FROM audit_events WHERE tenant_id = %s ORDER BY ts DESC LIMIT %s",
                    (tenant_id, limit),
                )
            return [
                {"event_id": e, "ts": str(ts), "session_id": s, "turn_id": t,
                 "stage": st, "user_id": u, "payload": p}
                for e, ts, s, t, st, u, p in await cur.fetchall()
            ]
        finally:
            await conn.close()
