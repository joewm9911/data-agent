"""会话运行时（架构文档 7.2/7.3、部署决策 D2/D4 的进程内实现）。

- pull 模型：回合只进会话专属队列，worker 持租约消费（唯一消费者）
- 转录 = 会话状态：每回合结束携 fencing token 增量写回 BlobStore
- 长回合心跳自动续租（7.2 租约）；回合内检查点写 turns/{id}/checkpoint.json
- controller：ensure（COLD→ACTIVE 水合）/ idle_sweep（IDLE→快照→销毁）
- 主动任务 = kind="proactive" 的回合，同队列同链路（7.4）
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol

from da_platform.memory import InMemoryBlobStore, InMemoryLeaseManager, InMemorySessionQueue
from da_platform.primitives import BlobStore, LeaseManager, SessionQueue
from da_types import UserIdentity

from da_runtime.lock import SessionBusyError, SessionTurnLock
from da_runtime.models import SessionMeta, Turn


class TurnExecutor(Protocol):
    """回合执行体（由分析引擎实现；runtime 不感知 LLM 细节——依赖倒置保持分层）。"""

    async def __call__(
        self,
        question: str,
        identity: UserIdentity,
        session_id: str,
        history: list[dict],
    ) -> tuple[str, list[dict]]:
        """返回 (回答文本, 新转录)。"""
        ...


@dataclass
class TurnOutcome:
    turn: Turn
    answer_text: str = ""
    error: str = ""


class SessionRuntime:
    """单个活跃会话的运行时（= 部署态中的会话容器，进程内形态）。"""

    def __init__(
        self,
        meta: SessionMeta,
        executor: TurnExecutor,
        queue: SessionQueue,
        blobs: BlobStore,
        lock: SessionTurnLock,
        worker_id: str,
    ) -> None:
        self.meta = meta
        self._executor = executor
        self._queue = queue
        self._blobs = blobs
        self._lock = lock
        self._worker_id = worker_id
        self.transcript: list[dict] = []
        self.last_active: datetime = datetime.now(UTC)

    @property
    def _snapshot_key(self) -> str:
        return f"sessions/{self.meta.session_id}/transcript.json"

    def _checkpoint_key(self, turn_id: str) -> str:
        return f"sessions/{self.meta.session_id}/turns/{turn_id}/checkpoint.json"

    def make_checkpointer(self, turn_id: str):
        """回合内检查点（7.2）：agent 每个工具轮结束调用，崩溃损失上限=半个工具轮。"""

        async def checkpoint(partial_transcript: list[dict]) -> None:
            await self._blobs.put(
                self._checkpoint_key(turn_id),
                json.dumps(partial_transcript, ensure_ascii=False).encode(),
            )

        return checkpoint

    async def load_checkpoint(self, turn_id: str) -> list[dict] | None:
        data = await self._blobs.get(self._checkpoint_key(turn_id))
        return json.loads(data) if data is not None else None

    async def hydrate(self) -> None:
        """WARMING：从 BlobStore 恢复转录（D4：文件内容跟会话走，不跟机器走）。"""
        data = await self._blobs.get(self._snapshot_key)
        if data is not None:
            self.transcript = json.loads(data)
        self.meta.state = "active"

    async def snapshot(self, fencing_token: int) -> None:
        await self._blobs.put(
            self._snapshot_key,
            json.dumps(self.transcript, ensure_ascii=False).encode(),
            fencing_token=fencing_token,
        )

    async def enqueue(self, turn: Turn) -> None:
        await self._queue.push(self.meta.session_id, turn.model_dump_json().encode())

    async def run_one_turn(
        self, identity: UserIdentity, timeout_seconds: float = 0.1
    ) -> TurnOutcome | None:
        """消费一个回合：租约 → 执行 → 回合结束增量写回（携 fencing token）。"""
        raw = await self._queue.pop(self.meta.session_id, timeout_seconds)
        if raw is None:
            return None
        turn = Turn.model_validate_json(raw)
        try:
            async with self._lock.hold(self.meta.session_id, self._worker_id) as lease:
                turn.status = "running"
                # 心跳自动续租：长回合（归因/多步分析）超过 ttl 也不丢租约
                current_lease = lease
                stop = asyncio.Event()

                async def heartbeat():
                    nonlocal current_lease
                    interval = max(self._lock.ttl_seconds * 0.4, 0.05)
                    while not stop.is_set():
                        with contextlib.suppress(asyncio.TimeoutError):
                            await asyncio.wait_for(stop.wait(), timeout=interval)
                        if stop.is_set():
                            return
                        current_lease = await self._lock.heartbeat(current_lease)

                hb_task = asyncio.create_task(heartbeat())
                try:
                    answer_text, transcript = await self._executor(
                        question=turn.input_text,
                        identity=identity,
                        session_id=self.meta.session_id,
                        history=self.transcript,
                    )
                finally:
                    stop.set()
                    await hb_task
                self.transcript = transcript
                await self.snapshot(fencing_token=current_lease.token)
                await self._blobs.delete(self._checkpoint_key(turn.turn_id))
                turn.status = "completed"
                self.last_active = datetime.now(UTC)
                return TurnOutcome(turn=turn, answer_text=answer_text)
        except SessionBusyError:
            # 回合放回队列（同会话串行；不同会话互不影响）
            await self._queue.push(self.meta.session_id, raw)
            return None
        except Exception as e:  # noqa: BLE001 - 回合失败不炸容器
            turn.status = "failed"
            return TurnOutcome(turn=turn, error=str(e))


@dataclass
class SessionController:
    """会话容器编排器（D3：无状态，视图可从存储重建）。进程内形态。"""

    executor: TurnExecutor
    queue: SessionQueue = field(default_factory=InMemorySessionQueue)
    blobs: BlobStore = field(default_factory=InMemoryBlobStore)
    leases: LeaseManager = field(default_factory=InMemoryLeaseManager)
    idle_seconds: float = 900.0
    _active: dict[str, SessionRuntime] = field(default_factory=dict)

    async def ensure(self, meta: SessionMeta, worker_id: str = "worker-1") -> SessionRuntime:
        """COLD → WARMING（水合）→ ACTIVE；已活跃则直接返回（亲和是优化不是正确性）。"""
        runtime = self._active.get(meta.session_id)
        if runtime is not None:
            return runtime
        meta.state = "warming"
        runtime = SessionRuntime(
            meta=meta,
            executor=self.executor,
            queue=self.queue,
            blobs=self.blobs,
            lock=SessionTurnLock(self.leases),
            worker_id=worker_id,
        )
        await runtime.hydrate()
        self._active[meta.session_id] = runtime
        return runtime

    async def idle_sweep(self, now: datetime | None = None) -> list[str]:
        """IDLE 回收：快照已在回合结束写过，直接销毁运行时（成本归零）。"""
        now = now or datetime.now(UTC)
        evicted = []
        for sid, runtime in list(self._active.items()):
            if (now - runtime.last_active).total_seconds() >= self.idle_seconds:
                runtime.meta.state = "cold"
                del self._active[sid]
                evicted.append(sid)
        return evicted

    def active_sessions(self) -> list[str]:
        return sorted(self._active)
