"""接入层。上层（语义层/分析引擎）只面对 Connector 接口，看不到具体数据源。"""

from da_connectors.base import (
    AccessDeniedError,
    Connector,
    ConnectorError,
    GuardRejectedError,
    QueryExecutionError,
)
from da_connectors.registry import get_connector_cls, register_connector

__all__ = [
    "AccessDeniedError",
    "Connector",
    "ConnectorError",
    "GuardRejectedError",
    "QueryExecutionError",
    "get_connector_cls",
    "register_connector",
]
