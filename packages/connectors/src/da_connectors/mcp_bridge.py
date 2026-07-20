"""MCP 适配器框架（架构文档 3.3）：接入成本外置的协议桥。

企业平台团队把内部系统包成实现四方法的 JSON-RPC 端点（MCP tool 形态），
本桥把它适配成标准 Connector。传输层可插拔（真实部署为 MCP client，测试为内存回调）。

方法契约（与 Connector 四接口一一对应，参数/返回均为 JSON dict）：
- execute(query, identity, guard) -> QueryResult
- get_metadata(scope) -> CatalogSnapshot
- get_query_history(window) -> {"queries": [HistoricalQuery, ...]}
- check_access(identity, objects) -> AccessDecision
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

from da_types import (
    AccessDecision,
    CatalogSnapshot,
    DataObject,
    GuardPolicy,
    HistoricalQuery,
    MetadataScope,
    Query,
    QueryResult,
    TimeWindow,
    UserIdentity,
)

from da_connectors.base import Connector, QueryExecutionError
from da_connectors.registry import register_connector

# 传输：method 名 + JSON 参数 → JSON 结果（真实实现 = MCP tool 调用）
Transport = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]


@register_connector("mcp")
class McpConnector(Connector):
    def __init__(self, source_id: str, transport: Transport, dialect: str = "ansi") -> None:
        self.source_id = source_id
        self.dialect = dialect
        self._call = transport

    async def execute(
        self, query: Query, identity: UserIdentity, guard: GuardPolicy
    ) -> QueryResult:
        try:
            payload = await self._call(
                "execute",
                {
                    "query": query.model_dump(),
                    "identity": identity.model_dump(),
                    "guard": guard.model_dump(),
                },
            )
        except Exception as e:  # noqa: BLE001
            raise QueryExecutionError(str(e)) from e
        return QueryResult.model_validate(payload)

    async def get_metadata(self, scope: MetadataScope) -> CatalogSnapshot:
        payload = await self._call("get_metadata", {"scope": scope.model_dump()})
        return CatalogSnapshot.model_validate(payload)

    async def get_query_history(  # type: ignore[override]
        self, window: TimeWindow
    ) -> AsyncIterator[HistoricalQuery]:
        payload = await self._call(
            "get_query_history", {"window": window.model_dump(mode="json")}
        )
        for item in payload.get("queries", []):
            yield HistoricalQuery.model_validate(item)

    async def check_access(
        self, identity: UserIdentity, objects: list[DataObject]
    ) -> AccessDecision:
        payload = await self._call(
            "check_access",
            {
                "identity": identity.model_dump(),
                "objects": [o.model_dump() for o in objects],
            },
        )
        return AccessDecision.model_validate(payload)
