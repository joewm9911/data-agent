"""SQLite 适配器：本地场景/单机模式/测试用。

同时是 Connector 抽象的第二个实现——证明上层完全不感知数据源差异。
只读硬保证：以 mode=ro 打开数据库文件，与语句级护栏双保险。
SQLite 无查询日志，get_query_history 返回空流（冷启动退化为 schema profiling 信号）。
"""

from __future__ import annotations

import sqlite3
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

from da_governance import prepare_statement
from da_types import (
    AccessDecision,
    CatalogSnapshot,
    ColumnMeta,
    ColumnSchema,
    DataObject,
    GuardPolicy,
    HistoricalQuery,
    MetadataScope,
    ObjectAccess,
    Query,
    QueryResult,
    QueryStats,
    TableMeta,
    TimeWindow,
    UserIdentity,
)

from da_connectors.base import (
    AccessDeniedError,
    Connector,
    GuardRejectedError,
    QueryExecutionError,
)
from da_connectors.registry import register_connector

LOGICAL_DATABASE = "main"


@register_connector("sqlite")
class SQLiteConnector(Connector):
    dialect = "sqlite"

    def __init__(self, source_id: str, db_path: str | Path) -> None:
        self.source_id = source_id
        self._db_path = Path(db_path)

    def _connect(self) -> sqlite3.Connection:
        # mode=ro：数据库层面的只读硬保证（铁律 P3：护栏之外的第二道防线）
        return sqlite3.connect(f"file:{self._db_path}?mode=ro", uri=True)

    async def execute(
        self, query: Query, identity: UserIdentity, guard: GuardPolicy
    ) -> QueryResult:
        decision = prepare_statement(query.statement, self.dialect, guard)
        if not decision.allowed:
            raise GuardRejectedError(decision.reason)
        assert decision.rewritten_statement is not None

        conn = self._connect()
        try:
            cursor = conn.execute(decision.rewritten_statement)
            rows = [list(r) for r in cursor.fetchall()]
            columns = [
                ColumnSchema(name=d[0], type="") for d in (cursor.description or [])
            ]
        except sqlite3.Error as e:
            raise QueryExecutionError(str(e)) from e
        finally:
            conn.close()

        return QueryResult(
            columns=columns,
            rows=rows,
            stats=QueryStats(scanned_rows=len(rows)),
            truncated=len(rows) >= guard.max_result_rows,
        )

    async def get_metadata(self, scope: MetadataScope) -> CatalogSnapshot:
        conn = self._connect()
        try:
            table_names = [
                r[0]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master "
                    "WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                ).fetchall()
                if not scope.tables or r[0] in scope.tables
            ]
            tables = []
            for name in table_names:
                cols = [
                    ColumnMeta(name=c[1], type=c[2] or "")
                    for c in conn.execute(f"PRAGMA table_info({name})").fetchall()
                ]
                row_count = conn.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
                tables.append(
                    TableMeta(
                        database=LOGICAL_DATABASE,
                        name=name,
                        engine="sqlite",
                        row_count=row_count,
                        columns=cols,
                    )
                )
        finally:
            conn.close()

        return CatalogSnapshot(
            source_id=self.source_id,
            captured_at=datetime.now(UTC),
            tables=tables,
        )

    async def get_query_history(  # type: ignore[override]
        self, window: TimeWindow
    ) -> AsyncIterator[HistoricalQuery]:
        return
        yield  # pragma: no cover - 使函数成为生成器；SQLite 无查询日志

    async def check_access(
        self, identity: UserIdentity, objects: list[DataObject]
    ) -> AccessDecision:
        if not objects:
            raise AccessDeniedError("empty object list")
        allowed_dbs = {
            d.strip()
            for d in identity.claims.get("allowed_databases", "").split(",")
            if d.strip()
        }
        results = [
            ObjectAccess(
                object=obj,
                allowed=obj.database in allowed_dbs,
                reason="" if obj.database in allowed_dbs else "database not in allowlist",
            )
            for obj in objects
        ]
        return AccessDecision(identity_user_id=identity.user_id, results=results)
