"""查询历史契约：语义层冷启动的第一优先级信号源（架构文档 4.2）。"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class TimeWindow(BaseModel):
    start: datetime
    end: datetime


class HistoricalQuery(BaseModel):
    query_id: str
    sql: str
    user: str = ""
    started_at: datetime
    duration_ms: int | None = None
    scanned_bytes: int | None = None
    status: str = "finished"
