"""Redis providers（10.2）：五原语的生产实现，语义与内存版一致（同一套一致性测试）。

- lease：Lua 保证原子性；fencing token 用独立 INCR 计数器保证单调
- queue：按 key 的 LIST（LPUSH/BRPOP）
- pubsub：Redis 原生 pub/sub
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import redis.asyncio as aioredis

from da_platform.primitives import Lease, StaleTokenError

DEFAULT_URL = "redis://localhost:6379/0"


def redis_url() -> str:
    return os.environ.get("DA_REDIS_URL", DEFAULT_URL)


def client(url: str | None = None) -> aioredis.Redis:
    return aioredis.from_url(url or redis_url())


# KEYS[1]=lease key, ARGV[1]=holder, ARGV[2]=token, ARGV[3]=ttl_ms
_ACQUIRE = """
if redis.call('EXISTS', KEYS[1]) == 1 then return nil end
redis.call('SET', KEYS[1], ARGV[1] .. ':' .. ARGV[2], 'PX', ARGV[3])
return ARGV[2]
"""

# KEYS[1]=lease key, ARGV[1]=holder:token, ARGV[2]=ttl_ms
_RENEW = """
if redis.call('GET', KEYS[1]) ~= ARGV[1] then return 0 end
redis.call('PEXPIRE', KEYS[1], ARGV[2])
return 1
"""

_RELEASE = """
if redis.call('GET', KEYS[1]) == ARGV[1] then redis.call('DEL', KEYS[1]) end
return 1
"""


class RedisLeaseManager:
    def __init__(self, r: aioredis.Redis | None = None, prefix: str = "da:lease:") -> None:
        self._r = r or client()
        self._prefix = prefix

    async def acquire(self, key: str, holder: str, ttl_seconds: float) -> Lease | None:
        token = int(await self._r.incr(f"{self._prefix}token:{key}"))
        ttl_ms = max(int(ttl_seconds * 1000), 1)
        got = await self._r.eval(
            _ACQUIRE, 1, f"{self._prefix}{key}", holder, str(token), str(ttl_ms)
        )
        if got is None:
            return None
        return Lease(
            key=key,
            holder=holder,
            token=token,
            expires_at=datetime.now(UTC) + timedelta(seconds=ttl_seconds),
        )

    async def renew(self, lease: Lease, ttl_seconds: float) -> Lease:
        ok = await self._r.eval(
            _RENEW, 1, f"{self._prefix}{lease.key}",
            f"{lease.holder}:{lease.token}", str(max(int(ttl_seconds * 1000), 1)),
        )
        if not int(ok):
            raise StaleTokenError(f"lease lost: {lease.key}")
        return lease.model_copy(
            update={"expires_at": datetime.now(UTC) + timedelta(seconds=ttl_seconds)}
        )

    async def release(self, lease: Lease) -> None:
        await self._r.eval(
            _RELEASE, 1, f"{self._prefix}{lease.key}", f"{lease.holder}:{lease.token}"
        )


class RedisSessionQueue:
    def __init__(self, r: aioredis.Redis | None = None, prefix: str = "da:q:") -> None:
        self._r = r or client()
        self._prefix = prefix

    async def push(self, key: str, item: bytes) -> None:
        await self._r.lpush(f"{self._prefix}{key}", item)

    async def pop(self, key: str, timeout_seconds: float) -> bytes | None:
        got = await self._r.brpop([f"{self._prefix}{key}"], timeout=max(timeout_seconds, 0.01))
        return got[1] if got else None

    async def depth(self, key: str) -> int:
        return int(await self._r.llen(f"{self._prefix}{key}"))


class RedisKeyValue:
    def __init__(self, r: aioredis.Redis | None = None, prefix: str = "da:kv:") -> None:
        self._r = r or client()
        self._prefix = prefix

    async def get(self, key: str) -> bytes | None:
        return await self._r.get(f"{self._prefix}{key}")

    async def set(self, key: str, value: bytes, ttl_seconds: float | None = None) -> None:
        if ttl_seconds is None:
            await self._r.set(f"{self._prefix}{key}", value)
        else:
            await self._r.set(f"{self._prefix}{key}", value, px=max(int(ttl_seconds * 1000), 1))

    async def delete(self, key: str) -> None:
        await self._r.delete(f"{self._prefix}{key}")


class RedisPubSub:
    def __init__(self, r: aioredis.Redis | None = None, prefix: str = "da:ch:") -> None:
        self._r = r or client()
        self._prefix = prefix

    async def publish(self, channel: str, data: bytes) -> None:
        await self._r.publish(f"{self._prefix}{channel}", data)

    async def subscribe(self, channel: str) -> AsyncIterator[bytes]:
        pubsub = self._r.pubsub()
        await pubsub.subscribe(f"{self._prefix}{channel}")
        try:
            async for message in pubsub.listen():
                if message["type"] == "message":
                    yield message["data"]
        finally:
            await pubsub.unsubscribe()
            await pubsub.aclose()
