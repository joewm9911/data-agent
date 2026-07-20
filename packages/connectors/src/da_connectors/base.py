"""Connector 抽象接口（架构文档 3.1 的代码化契约）。

四接口：execute / get_metadata / get_query_history / check_access。
任何适配器（直连/网关中台/BI 资产/MCP）必须完整实现并通过 conformance 测试。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator

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


class ConnectorError(Exception):
    """接入层错误基类。"""


class QueryExecutionError(ConnectorError):
    """数据源执行失败（语法/超时/资源）。message 可回流给 agent 用于自纠错。"""


class GuardRejectedError(ConnectorError):
    """护栏拒绝。reason 记入审计，不回显内部细节给终端用户。"""


class AccessDeniedError(ConnectorError):
    """权限拒绝。注意语义层权限感知原则：对终端用户表现为'没有找到相关数据'。"""


class Connector(ABC):
    """所有方法强制携带 identity（铁律 P3）与 guard（3.4 护栏不可绕过）。"""

    source_id: str
    dialect: str

    @abstractmethod
    async def execute(
        self, query: Query, identity: UserIdentity, guard: GuardPolicy
    ) -> QueryResult:
        """以 identity 身份执行经护栏改写后的查询。"""

    @abstractmethod
    async def get_metadata(self, scope: MetadataScope) -> CatalogSnapshot:
        """拉取元数据快照，供 profiling 与语义层冷启动。"""

    @abstractmethod
    def get_query_history(self, window: TimeWindow) -> AsyncIterator[HistoricalQuery]:
        """流式拉取历史查询（冷启动第一信号源）。"""

    @abstractmethod
    async def check_access(
        self, identity: UserIdentity, objects: list[DataObject]
    ) -> AccessDecision:
        """权限判定：回调企业权限体系（数仓原生权限/中台/权限中心）。"""
