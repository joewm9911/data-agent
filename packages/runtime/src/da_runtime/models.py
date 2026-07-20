"""会话与回合模型（架构文档 7.1/7.2、部署决策 D2）。"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field

# 会话容器生命周期状态机（D2）
SessionState = Literal["cold", "warming", "active", "idle"]

TurnStatus = Literal["queued", "running", "completed", "failed"]


class SessionMeta(BaseModel):
    """业务层会话元数据（Postgres 层）。SDK 会话目录是运行时工作副本（7.6）。"""

    session_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    tenant_id: str
    user_id: str
    state: SessionState = "cold"
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    # SDK 会话目录快照在 BlobStore 中的 key 前缀
    snapshot_prefix: str = ""


class Turn(BaseModel):
    """回合 = 运行时原子单位（铁律 P4）。主动任务 = 无用户消息的回合（7.4）。"""

    turn_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    session_id: str
    kind: Literal["user_message", "proactive"] = "user_message"
    input_text: str = ""
    status: TurnStatus = "queued"
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
