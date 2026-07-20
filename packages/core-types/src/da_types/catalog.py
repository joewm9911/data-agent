"""元数据契约：供 schema profiling 与语义层冷启动。"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class MetadataScope(BaseModel):
    """元数据拉取范围。空列表 = 该维度不限制。"""

    databases: list[str] = Field(default_factory=list)
    tables: list[str] = Field(default_factory=list)


class ColumnMeta(BaseModel):
    name: str
    type: str
    comment: str = ""
    # profiling 统计（值分布/基数等）后续扩展为结构化模型
    stats: dict[str, str] = Field(default_factory=dict)


class TableMeta(BaseModel):
    database: str
    name: str
    engine: str = ""
    comment: str = ""
    row_count: int | None = None
    columns: list[ColumnMeta] = Field(default_factory=list)


class CatalogSnapshot(BaseModel):
    source_id: str
    captured_at: datetime
    tables: list[TableMeta]
