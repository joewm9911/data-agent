"""五个最小原语接口（架构文档 10.2）。

任何 provider 实现（Redis/NATS/S3/MinIO/内存）必须通过一致性测试套件才能注册。
lease 语义是 D4 脑裂防护的基础，语义以测试为准，不以实现为准。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime
from typing import Protocol

from pydantic import BaseModel


class StaleTokenError(Exception):
    """携带过期 fencing token 的写入被拒绝（D4：旧容器最坏白跑一回合，状态不被污染）。"""


class Lease(BaseModel):
    key: str
    holder: str
    # 单调递增 fencing token：同一 key 上每次成功 acquire 严格递增
    token: int
    expires_at: datetime


class LeaseManager(Protocol):
    """会话租约：保证同一会话全局唯一消费者。"""

    async def acquire(self, key: str, holder: str, ttl_seconds: float) -> Lease | None:
        """抢占租约。已被持有且未过期时返回 None。"""
        ...

    async def renew(self, lease: Lease, ttl_seconds: float) -> Lease:
        """心跳续租。租约已易主时抛 StaleTokenError。"""
        ...

    async def release(self, lease: Lease) -> None:
        """释放租约。已易主时静默忽略。"""
        ...


class SessionQueue(Protocol):
    """按 key（session_id）有序的队列。pull 模型：消费者自取，网关不选机器。"""

    async def push(self, key: str, item: bytes) -> None: ...

    async def pop(self, key: str, timeout_seconds: float) -> bytes | None:
        """阻塞式取出队头；超时返回 None。"""
        ...

    async def depth(self, key: str) -> int: ...


class KeyValue(Protocol):
    async def get(self, key: str) -> bytes | None: ...

    async def set(self, key: str, value: bytes, ttl_seconds: float | None = None) -> None: ...

    async def delete(self, key: str) -> None: ...


class PubSub(Protocol):
    """流式频道：worker 发布 token，任意网关节点订阅（执行者与连接持有者解耦）。"""

    async def publish(self, channel: str, data: bytes) -> None: ...

    def subscribe(self, channel: str) -> AsyncIterator[bytes]: ...


class BlobStore(Protocol):
    """对象存储。put 携带 fencing token 做条件写：token 小于已记录值时拒绝（D4）。"""

    async def put(self, key: str, data: bytes, fencing_token: int | None = None) -> None: ...

    async def get(self, key: str) -> bytes | None: ...

    async def delete(self, key: str) -> None: ...

    async def list_keys(self, prefix: str) -> list[str]: ...
