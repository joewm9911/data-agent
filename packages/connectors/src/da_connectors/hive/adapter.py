"""Hive 直连适配器（架构文档 3.2 直连模式，HiveServer2 协议）。

- 只读保证：Hive 无会话级 readonly 设置，依赖语句级护栏（sqlglot hive 方言校验）
  + 建议企业侧用 Ranger/只读账号做第二道防线（铁律 P3 的数据库层）
- 查询历史：HiveServer2 不暴露标准查询日志表，返回空流；
  企业有 Ranger 审计/EMR 日志时经 MCP 桥或自定义 history_provider 注入
- 依赖：pyhive（optional extra "hive"），经 client_factory 延迟注入，
  无 pyhive 时包仍可导入（单测用 fake client）
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime
from typing import Any

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

CredentialsResolver = Callable[[UserIdentity], dict[str, Any]]
HistoryProvider = Callable[[TimeWindow], list[HistoricalQuery]]


@register_connector("hive")
class HiveConnector(Connector):
    dialect = "hive"

    def __init__(
        self,
        source_id: str,
        credentials_resolver: CredentialsResolver,
        client_factory: Callable[..., Any] | None = None,
        history_provider: HistoryProvider | None = None,
        database: str = "default",
    ) -> None:
        self.source_id = source_id
        self._resolve_credentials = credentials_resolver
        self._client_factory = client_factory or _default_client_factory
        self._history_provider = history_provider
        self._database = database

    def _cursor(self, identity: UserIdentity):
        conn = self._client_factory(**self._resolve_credentials(identity))
        return conn.cursor()

    async def execute(
        self, query: Query, identity: UserIdentity, guard: GuardPolicy
    ) -> QueryResult:
        decision = prepare_statement(query.statement, self.dialect, guard)
        if not decision.allowed:
            raise GuardRejectedError(decision.reason)
        assert decision.rewritten_statement is not None

        cursor = self._cursor(identity)
        try:
            cursor.execute(decision.rewritten_statement)
            rows = [list(r) for r in cursor.fetchall()]
            columns = [
                ColumnSchema(name=d[0].split(".")[-1], type=str(d[1]))
                for d in (cursor.description or [])
            ]
        except Exception as e:  # noqa: BLE001 - 驱动异常统一收敛为契约错误
            raise QueryExecutionError(str(e)) from e
        finally:
            cursor.close()

        return QueryResult(
            columns=columns,
            rows=rows,
            stats=QueryStats(scanned_rows=len(rows)),
            truncated=len(rows) >= guard.max_result_rows,
        )

    async def get_metadata(self, scope: MetadataScope) -> CatalogSnapshot:
        cursor = self._cursor(_system_identity())
        try:
            cursor.execute("SHOW TABLES")
            table_names = [
                r[0] for r in cursor.fetchall()
                if not scope.tables or r[0] in scope.tables
            ]
            tables = []
            for name in table_names:
                cursor.execute(f"DESCRIBE {name}")
                cols = []
                for row in cursor.fetchall():
                    col_name = (row[0] or "").strip()
                    # DESCRIBE 输出中分区信息段以 # 开头或空行分隔，跳过
                    if not col_name or col_name.startswith("#"):
                        break
                    cols.append(
                        ColumnMeta(
                            name=col_name,
                            type=(row[1] or "").strip(),
                            comment=(row[2] or "").strip() if len(row) > 2 else "",
                        )
                    )
                tables.append(
                    TableMeta(
                        database=self._database, name=name, engine="hive", columns=cols
                    )
                )
        finally:
            cursor.close()

        return CatalogSnapshot(
            source_id=self.source_id,
            captured_at=datetime.now(UTC),
            tables=tables,
        )

    async def get_query_history(  # type: ignore[override]
        self, window: TimeWindow
    ) -> AsyncIterator[HistoricalQuery]:
        if self._history_provider is not None:
            for hq in self._history_provider(window):
                yield hq

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


def _system_identity() -> UserIdentity:
    return UserIdentity(tenant_id="_system", user_id="_metadata_reader")


def _default_client_factory(**kwargs: Any):
    from pyhive import hive  # 延迟导入：optional extra "hive"

    return hive.connect(**kwargs)
