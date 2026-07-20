"""跨模块共享契约。所有跨包通信只允许使用本包中的类型。"""

from da_types.catalog import CatalogSnapshot, ColumnMeta, MetadataScope, TableMeta
from da_types.history import HistoricalQuery, TimeWindow
from da_types.identity import AccessDecision, DataObject, ObjectAccess, UserIdentity
from da_types.query import (
    ColumnSchema,
    GuardDecision,
    GuardPolicy,
    Query,
    QueryResult,
    QueryStats,
)

__all__ = [
    "AccessDecision",
    "CatalogSnapshot",
    "ColumnMeta",
    "ColumnSchema",
    "DataObject",
    "GuardDecision",
    "GuardPolicy",
    "HistoricalQuery",
    "MetadataScope",
    "ObjectAccess",
    "Query",
    "QueryResult",
    "QueryStats",
    "TableMeta",
    "TimeWindow",
    "UserIdentity",
]
