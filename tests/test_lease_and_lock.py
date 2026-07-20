"""Lease/锁语义一致性测试（10.2：语义以测试为准）。

覆盖 D4 三层机制的可测部分：唯一消费者、fencing token 单调、旧 token 条件写拒绝。
任何新的 LeaseManager/BlobStore provider 都必须通过本套测试。
"""

import pytest
from da_platform.memory import InMemoryBlobStore, InMemoryLeaseManager
from da_platform.primitives import StaleTokenError
from da_runtime import SessionTurnLock
from da_runtime.lock import SessionBusyError


async def test_lease_exclusive_and_token_monotonic():
    leases = InMemoryLeaseManager()
    l1 = await leases.acquire("s1", "worker-a", ttl_seconds=60)
    assert l1 is not None

    # 未过期时他人抢不到
    assert await leases.acquire("s1", "worker-b", ttl_seconds=60) is None

    await leases.release(l1)
    l2 = await leases.acquire("s1", "worker-b", ttl_seconds=60)
    assert l2 is not None
    assert l2.token > l1.token  # fencing token 严格递增


async def test_renew_after_takeover_raises():
    leases = InMemoryLeaseManager()
    l1 = await leases.acquire("s1", "worker-a", ttl_seconds=0)  # 立即过期
    assert l1 is not None
    l2 = await leases.acquire("s1", "worker-b", ttl_seconds=60)  # 易主
    assert l2 is not None

    with pytest.raises(StaleTokenError):
        await leases.renew(l1, ttl_seconds=60)


async def test_stale_token_blob_write_rejected():
    blobs = InMemoryBlobStore()
    await blobs.put("session/s1/snapshot", b"v2", fencing_token=2)
    with pytest.raises(StaleTokenError):
        await blobs.put("session/s1/snapshot", b"old", fencing_token=1)
    assert await blobs.get("session/s1/snapshot") == b"v2"


async def test_session_turn_lock_serializes():
    lock = SessionTurnLock(InMemoryLeaseManager())
    async with lock.hold("s1", "worker-a") as lease:
        assert lease.token >= 1
        with pytest.raises(SessionBusyError):
            async with lock.hold("s1", "worker-b"):
                pass
    # 释放后可再次获取
    async with lock.hold("s1", "worker-b") as lease2:
        assert lease2.token > lease.token
