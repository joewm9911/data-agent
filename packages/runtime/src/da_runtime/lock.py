"""会话串行锁（M0 不可后补项）。

同一会话同时只允许一个回合执行；持锁期间获得 fencing token，
所有状态写回（BlobStore 快照）必须携带该 token（D4 脑裂防护）。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from da_platform.primitives import Lease, LeaseManager


class SessionBusyError(Exception):
    """该会话已有回合在执行。调用方应将回合留在队列而不是并发执行。"""


class SessionTurnLock:
    def __init__(self, leases: LeaseManager, ttl_seconds: float = 120.0) -> None:
        self._leases = leases
        self._ttl = ttl_seconds

    @asynccontextmanager
    async def hold(self, session_id: str, holder: str) -> AsyncIterator[Lease]:
        """持有会话锁执行一个回合。yield 的 Lease.token 即写回用 fencing token。"""
        lease = await self._leases.acquire(f"turn:{session_id}", holder, self._ttl)
        if lease is None:
            raise SessionBusyError(session_id)
        try:
            yield lease
        finally:
            await self._leases.release(lease)

    async def heartbeat(self, lease: Lease) -> Lease:
        """长回合执行中的心跳续租（执行超过 ttl 的归因任务必须周期调用）。"""
        return await self._leases.renew(lease, self._ttl)
