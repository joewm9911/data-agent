"""全链路审计（架构文档 8.1）。

审计链：who → 自然语言问题 → 生成语句 → 护栏裁决 → 以谁身份执行 → 扫描/返回量 → 结论摘要 → 反馈。
append-only：审计事件一经写入不可修改。
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, Protocol

from da_types import UserIdentity
from pydantic import BaseModel, Field

AuditStage = Literal[
    "question",     # 用户自然语言提问
    "generation",   # agent 生成语句/IR
    "guard",        # 护栏裁决
    "execution",    # 数据源执行（含扫描量/返回行数）
    "presentation", # 呈现给用户的结论摘要
    "feedback",     # 用户确认/纠正
]


class AuditEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    ts: datetime = Field(default_factory=lambda: datetime.now(UTC))
    tenant_id: str
    session_id: str
    turn_id: str
    stage: AuditStage
    identity: UserIdentity
    payload: dict = Field(default_factory=dict)


class AuditSink(Protocol):
    async def append(self, event: AuditEvent) -> None: ...


class InMemoryAuditSink:
    def __init__(self) -> None:
        self.events: list[AuditEvent] = []

    async def append(self, event: AuditEvent) -> None:
        self.events.append(event)


class JsonlAuditSink:
    """本地 JSONL 审计（单机模式/测试）。生产环境由 Postgres 不可变表 + 企业审计平台推送实现。"""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)

    async def append(self, event: AuditEvent) -> None:
        with self._path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event.model_dump(mode="json"), ensure_ascii=False) + "\n")
