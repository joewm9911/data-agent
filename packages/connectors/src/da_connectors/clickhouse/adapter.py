"""ClickHouse 直连适配器（架构文档 3.2 直连模式）。

要点：
- 护栏先行：语句先经 da_governance.prepare_statement 改写，再叠加 CK 侧硬限制
  （readonly=1 / max_execution_time / max_result_rows），双保险。
- system.query_log 即查询历史：冷启动第一信号源开箱即得。
- 纯函数与 IO 分离：SQL 构造为模块级纯函数，可独立单测（无需 CK 服务）。
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

# ---------------------------------------------------------------------------
# 纯函数：SQL 构造（可独立单测）
# ---------------------------------------------------------------------------

SYSTEM_DATABASES = ("system", "INFORMATION_SCHEMA", "information_schema")


def build_tables_query(scope: MetadataScope) -> str:
    where = [
        "database NOT IN ('system', 'INFORMATION_SCHEMA', 'information_schema')"
    ]
    if scope.databases:
        dbs = ", ".join(f"'{d}'" for d in scope.databases)
        where.append(f"database IN ({dbs})")
    if scope.tables:
        tbls = ", ".join(f"'{t}'" for t in scope.tables)
        where.append(f"name IN ({tbls})")
    return (
        "SELECT database, name, engine, comment, total_rows "
        "FROM system.tables WHERE " + " AND ".join(where)
    )


def build_columns_query(scope: MetadataScope) -> str:
    where = [
        "database NOT IN ('system', 'INFORMATION_SCHEMA', 'information_schema')"
    ]
    if scope.databases:
        dbs = ", ".join(f"'{d}'" for d in scope.databases)
        where.append(f"database IN ({dbs})")
    if scope.tables:
        tbls = ", ".join(f"'{t}'" for t in scope.tables)
        where.append(f"table IN ({tbls})")
    return (
        "SELECT database, table, name, type, comment "
        "FROM system.columns WHERE " + " AND ".join(where)
    )


def build_query_log_query(window: TimeWindow) -> str:
    """SELECT 完成态查询，排除系统内部与本产品自身的查询。"""
    start = window.start.strftime("%Y-%m-%d %H:%M:%S")
    end = window.end.strftime("%Y-%m-%d %H:%M:%S")
    return (
        "SELECT query_id, query, user, event_time, query_duration_ms, read_bytes "
        "FROM system.query_log "
        "WHERE type = 'QueryFinish' AND query_kind = 'Select' "
        "AND is_initial_query = 1 "
        f"AND event_time BETWEEN '{start}' AND '{end}' "
        "AND query NOT ILIKE '%system.%' "
        "ORDER BY event_time"
    )


def guard_settings(guard: GuardPolicy) -> dict[str, Any]:
    """CK 侧硬限制：与语句级护栏双保险。"""
    settings: dict[str, Any] = {
        "readonly": 1 if guard.read_only else 0,
        "max_execution_time": guard.max_execution_seconds,
        "max_result_rows": guard.max_result_rows,
        "result_overflow_mode": "break",
    }
    if guard.max_scan_bytes is not None:
        settings["max_bytes_to_read"] = guard.max_scan_bytes
    return settings


# ---------------------------------------------------------------------------
# 适配器
# ---------------------------------------------------------------------------

# 凭证解析器：identity → clickhouse-connect 连接参数（铁律 P3：以真实用户身份连接）。
# 生产实现由 SecretProvider 注入每用户凭证；测试注入 fake client。
CredentialsResolver = Callable[[UserIdentity], dict[str, Any]]


@register_connector("clickhouse")
class ClickHouseConnector(Connector):
    dialect = "clickhouse"

    def __init__(
        self,
        source_id: str,
        credentials_resolver: CredentialsResolver,
        client_factory: Callable[..., Any] | None = None,
    ) -> None:
        self.source_id = source_id
        self._resolve_credentials = credentials_resolver
        self._client_factory = client_factory or _default_client_factory

    def _client(self, identity: UserIdentity):
        return self._client_factory(**self._resolve_credentials(identity))

    async def execute(
        self, query: Query, identity: UserIdentity, guard: GuardPolicy
    ) -> QueryResult:
        decision = prepare_statement(query.statement, self.dialect, guard)
        if not decision.allowed:
            raise GuardRejectedError(decision.reason)
        assert decision.rewritten_statement is not None

        client = self._client(identity)
        try:
            result = client.query(
                decision.rewritten_statement, settings=guard_settings(guard)
            )
        except Exception as e:  # noqa: BLE001 - 驱动异常统一收敛为契约错误
            raise QueryExecutionError(str(e)) from e

        rows = [list(r) for r in result.result_rows]
        return QueryResult(
            columns=[
                ColumnSchema(name=n, type=str(t))
                for n, t in zip(result.column_names, result.column_types, strict=False)
            ],
            rows=rows,
            stats=QueryStats(),
            truncated=len(rows) >= guard.max_result_rows,
        )

    async def get_metadata(self, scope: MetadataScope) -> CatalogSnapshot:
        client = self._client(_system_identity())
        tables_rows = client.query(build_tables_query(scope)).result_rows
        columns_rows = client.query(build_columns_query(scope)).result_rows

        columns_by_table: dict[tuple[str, str], list[ColumnMeta]] = {}
        for db, table, name, type_, comment in columns_rows:
            columns_by_table.setdefault((db, table), []).append(
                ColumnMeta(name=name, type=type_, comment=comment or "")
            )

        tables = [
            TableMeta(
                database=db,
                name=name,
                engine=engine or "",
                comment=comment or "",
                row_count=int(total_rows) if total_rows is not None else None,
                columns=columns_by_table.get((db, name), []),
            )
            for db, name, engine, comment, total_rows in tables_rows
        ]
        return CatalogSnapshot(
            source_id=self.source_id,
            captured_at=datetime.now(UTC),
            tables=tables,
        )

    async def get_query_history(  # type: ignore[override]
        self, window: TimeWindow
    ) -> AsyncIterator[HistoricalQuery]:
        client = self._client(_system_identity())
        for qid, sql, user, event_time, duration_ms, read_bytes in client.query(
            build_query_log_query(window)
        ).result_rows:
            yield HistoricalQuery(
                query_id=str(qid),
                sql=sql,
                user=user or "",
                started_at=event_time,
                duration_ms=int(duration_ms) if duration_ms is not None else None,
                scanned_bytes=int(read_bytes) if read_bytes is not None else None,
            )

    async def check_access(
        self, identity: UserIdentity, objects: list[DataObject]
    ) -> AccessDecision:
        """M0：白名单策略（渐进授权 6.3 的最简形态）。

        白名单来自 identity.claims["allowed_databases"]（逗号分隔），由权限平面注入。
        后续演进为回调数仓原生 RBAC / 企业权限中心。
        """
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
        if not results:
            raise AccessDeniedError("empty object list")
        return AccessDecision(identity_user_id=identity.user_id, results=results)


def _system_identity() -> UserIdentity:
    """元数据/历史拉取使用系统身份（只读、仅接触 system 表），与用户查询路径分离。"""
    return UserIdentity(tenant_id="_system", user_id="_metadata_reader")


def _default_client_factory(**kwargs: Any):
    import clickhouse_connect  # 延迟导入：无 driver 时包仍可导入（optional extra）

    return clickhouse_connect.get_client(**kwargs)
