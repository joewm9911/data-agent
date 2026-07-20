"""内存版 provider：用于测试与单机模式。语义与生产 provider 完全一致（过同一套一致性测试）。"""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

from da_platform.primitives import Lease, StaleTokenError


class InMemoryLeaseManager:
    def __init__(self) -> None:
        self._leases: dict[str, Lease] = {}
        self._tokens: dict[str, int] = defaultdict(int)
        self._lock = asyncio.Lock()

    async def acquire(self, key: str, holder: str, ttl_seconds: float) -> Lease | None:
        async with self._lock:
            current = self._leases.get(key)
            now = datetime.now(UTC)
            if current is not None and current.expires_at > now:
                return None
            self._tokens[key] += 1
            lease = Lease(
                key=key,
                holder=holder,
                token=self._tokens[key],
                expires_at=now + timedelta(seconds=ttl_seconds),
            )
            self._leases[key] = lease
            return lease

    async def renew(self, lease: Lease, ttl_seconds: float) -> Lease:
        async with self._lock:
            current = self._leases.get(lease.key)
            if current is None or current.token != lease.token:
                raise StaleTokenError(f"lease lost: {lease.key}")
            renewed = current.model_copy(
                update={"expires_at": datetime.now(UTC) + timedelta(seconds=ttl_seconds)}
            )
            self._leases[lease.key] = renewed
            return renewed

    async def release(self, lease: Lease) -> None:
        async with self._lock:
            current = self._leases.get(lease.key)
            if current is not None and current.token == lease.token:
                del self._leases[lease.key]


class InMemorySessionQueue:
    def __init__(self) -> None:
        self._queues: dict[str, asyncio.Queue[bytes]] = defaultdict(asyncio.Queue)

    async def push(self, key: str, item: bytes) -> None:
        await self._queues[key].put(item)

    async def pop(self, key: str, timeout_seconds: float) -> bytes | None:
        try:
            return await asyncio.wait_for(self._queues[key].get(), timeout=timeout_seconds)
        except TimeoutError:
            return None

    async def depth(self, key: str) -> int:
        return self._queues[key].qsize()


class InMemoryKeyValue:
    def __init__(self) -> None:
        self._data: dict[str, tuple[bytes, float | None]] = {}

    async def get(self, key: str) -> bytes | None:
        entry = self._data.get(key)
        if entry is None:
            return None
        value, expires = entry
        if expires is not None and time.monotonic() > expires:
            del self._data[key]
            return None
        return value

    async def set(self, key: str, value: bytes, ttl_seconds: float | None = None) -> None:
        expires = time.monotonic() + ttl_seconds if ttl_seconds is not None else None
        self._data[key] = (value, expires)

    async def delete(self, key: str) -> None:
        self._data.pop(key, None)


class InMemoryPubSub:
    def __init__(self) -> None:
        self._subscribers: dict[str, list[asyncio.Queue[bytes]]] = defaultdict(list)

    async def publish(self, channel: str, data: bytes) -> None:
        for q in self._subscribers[channel]:
            await q.put(data)

    async def subscribe(self, channel: str) -> AsyncIterator[bytes]:
        q: asyncio.Queue[bytes] = asyncio.Queue()
        self._subscribers[channel].append(q)
        try:
            while True:
                yield await q.get()
        finally:
            self._subscribers[channel].remove(q)


class InMemoryBlobStore:
    def __init__(self) -> None:
        self._blobs: dict[str, bytes] = {}
        self._tokens: dict[str, int] = {}

    async def put(self, key: str, data: bytes, fencing_token: int | None = None) -> None:
        if fencing_token is not None:
            recorded = self._tokens.get(key, -1)
            if fencing_token < recorded:
                raise StaleTokenError(
                    f"stale fencing token {fencing_token} < {recorded} for {key}"
                )
            self._tokens[key] = fencing_token
        self._blobs[key] = data

    async def get(self, key: str) -> bytes | None:
        return self._blobs.get(key)

    async def delete(self, key: str) -> None:
        self._blobs.pop(key, None)
        self._tokens.pop(key, None)

    async def list_keys(self, prefix: str) -> list[str]:
        return sorted(k for k in self._blobs if k.startswith(prefix))
