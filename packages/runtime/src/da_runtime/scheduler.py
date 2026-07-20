"""定时调度器（5.5 主动层的触发环 / 7.4）：零依赖 asyncio 实现。

调度器只做一件事：到点把 proactive 回合投进会话队列——执行链路完全复用。
生产部署可替换为 K8s CronJob（投递同一队列），本调度器服务单机/进程内形态。
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from da_types import UserIdentity

from da_runtime.models import SessionMeta, Turn
from da_runtime.session_runtime import SessionController


@dataclass
class ScheduledJob:
    name: str
    # 触发时要投递的回合输入（如 "[晨报] 工单量异常检查"）
    turn_input: str
    session_id: str
    identity: UserIdentity
    interval_seconds: float | None = None  # 间隔模式
    daily_at: str | None = None            # "HH:MM" 每日模式（UTC）
    last_fired: datetime | None = None

    def due(self, now: datetime) -> bool:
        if self.interval_seconds is not None:
            if self.last_fired is None:
                return True
            return (now - self.last_fired).total_seconds() >= self.interval_seconds
        if self.daily_at is not None:
            hh, mm = self.daily_at.split(":")
            fired_today = (
                self.last_fired is not None and self.last_fired.date() == now.date()
            )
            return (not fired_today) and (now.hour, now.minute) >= (int(hh), int(mm))
        return False


@dataclass
class Scheduler:
    controller: SessionController
    jobs: list[ScheduledJob] = field(default_factory=list)
    # 回合完成后的分发钩子（IM 推送等），可为 None
    on_result: Callable[[ScheduledJob, str], Awaitable[None]] | None = None

    def add(self, job: ScheduledJob) -> None:
        self.jobs.append(job)

    async def tick(self, now: datetime | None = None) -> list[str]:
        """检查所有任务，到点的投递 proactive 回合并执行。返回触发的任务名。"""
        now = now or datetime.now(UTC)
        fired = []
        for job in self.jobs:
            if not job.due(now):
                continue
            job.last_fired = now
            fired.append(job.name)
            meta = SessionMeta(
                session_id=job.session_id,
                tenant_id=job.identity.tenant_id,
                user_id=job.identity.user_id,
            )
            runtime = await self.controller.ensure(meta)
            await runtime.enqueue(
                Turn(session_id=job.session_id, kind="proactive", input_text=job.turn_input)
            )
            outcome = await runtime.run_one_turn(job.identity, timeout_seconds=1.0)
            if outcome and self.on_result is not None:
                await self.on_result(job, outcome.answer_text)
        return fired

    async def run_forever(self, poll_seconds: float = 30.0) -> None:
        while True:
            await self.tick()
            await asyncio.sleep(poll_seconds)
