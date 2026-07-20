"""查询 IR 与护栏契约。上层永远面对 Query IR，方言转译发生在适配器内部。"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class Query(BaseModel):
    """查询中间表示。M0 仅支持 sql 形态；网关/中台模式将扩展 api_call 形态。"""

    kind: Literal["sql"] = "sql"
    statement: str
    dialect: str = "clickhouse"
    params: dict[str, Any] = Field(default_factory=dict)


class GuardPolicy(BaseModel):
    """查询护栏策略（架构文档 3.4）。强制注入，适配器不得绕过。"""

    read_only: bool = True
    max_result_rows: int = 10_000
    max_execution_seconds: int = 60
    max_scan_bytes: int | None = None
    force_limit: bool = True
    # 聚合推理越权防御（6.2）：敏感域最小聚合行数，M0 预留
    min_agg_rows: int | None = None


class GuardDecision(BaseModel):
    allowed: bool
    reason: str = ""
    # 护栏可能改写语句（注入 LIMIT 等），执行必须使用改写后的语句
    rewritten_statement: str | None = None


class ColumnSchema(BaseModel):
    name: str
    type: str


class QueryStats(BaseModel):
    scanned_rows: int | None = None
    scanned_bytes: int | None = None
    duration_ms: int | None = None


class QueryResult(BaseModel):
    columns: list[ColumnSchema]
    rows: list[list[Any]]
    stats: QueryStats = Field(default_factory=QueryStats)
    truncated: bool = False
