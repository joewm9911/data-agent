"""生产 provider 一致性测试（10.2：语义以测试为准，与内存版同一套断言）。

Redis/Postgres 不可用时自动跳过（CI 无服务时跳过，本地/生产环境必须全过）。
"""

import uuid

import pytest
from da_platform.fsblob import FileSystemBlobStore
from da_platform.primitives import StaleTokenError


def _redis_available() -> bool:
    try:
        import redis

        redis.Redis(socket_connect_timeout=0.5).ping()
        return True
    except Exception:  # noqa: BLE001
        return False


def _pg_available() -> bool:
    try:
        import psycopg

        with psycopg.connect("postgresql://localhost/data_agent", connect_timeout=2):
            return True
    except Exception:  # noqa: BLE001
        return False


redis_required = pytest.mark.skipif(not _redis_available(), reason="redis 未运行")
pg_required = pytest.mark.skipif(not _pg_available(), reason="postgres 未运行")


@redis_required
async def test_redis_lease_semantics():
    from da_platform.redis_providers import RedisLeaseManager

    leases = RedisLeaseManager(prefix=f"t:{uuid.uuid4().hex}:")
    key = "s1"
    l1 = await leases.acquire(key, "worker-a", ttl_seconds=30)
    assert l1 is not None
    assert await leases.acquire(key, "worker-b", ttl_seconds=30) is None  # 排他

    renewed = await leases.renew(l1, ttl_seconds=30)
    assert renewed.token == l1.token

    await leases.release(l1)
    l2 = await leases.acquire(key, "worker-b", ttl_seconds=30)
    assert l2 is not None and l2.token > l1.token  # fencing 单调

    with pytest.raises(StaleTokenError):
        await leases.renew(l1, ttl_seconds=30)  # 易主后旧租约续租被拒


@redis_required
async def test_redis_queue_and_kv():
    from da_platform.redis_providers import RedisKeyValue, RedisSessionQueue

    q = RedisSessionQueue(prefix=f"t:{uuid.uuid4().hex}:")
    await q.push("s1", b"a")
    await q.push("s1", b"b")
    assert await q.depth("s1") == 2
    assert await q.pop("s1", 1.0) == b"a"  # FIFO
    assert await q.pop("s1", 1.0) == b"b"
    assert await q.pop("s1", 0.05) is None

    kv = RedisKeyValue(prefix=f"t:{uuid.uuid4().hex}:")
    await kv.set("k", b"v")
    assert await kv.get("k") == b"v"
    await kv.delete("k")
    assert await kv.get("k") is None


@redis_required
async def test_redis_pubsub_decouples_publisher_subscriber():
    import asyncio

    from da_platform.redis_providers import RedisPubSub

    ps = RedisPubSub(prefix=f"t:{uuid.uuid4().hex}:")
    received = []

    async def consume():
        async for msg in ps.subscribe("c1"):
            received.append(msg)
            if len(received) == 2:
                break

    task = asyncio.create_task(consume())
    await asyncio.sleep(0.2)  # 等订阅建立
    await ps.publish("c1", b"t1")
    await ps.publish("c1", b"t2")
    await asyncio.wait_for(task, timeout=5)
    assert received == [b"t1", b"t2"]


async def test_fs_blobstore_fencing(tmp_path):
    blobs = FileSystemBlobStore(tmp_path)
    await blobs.put("sessions/s1/x.json", b"v2", fencing_token=2)
    with pytest.raises(StaleTokenError):
        await blobs.put("sessions/s1/x.json", b"old", fencing_token=1)
    assert await blobs.get("sessions/s1/x.json") == b"v2"
    assert await blobs.list_keys("sessions/") == ["sessions/s1/x.json"]
    await blobs.delete("sessions/s1/x.json")
    assert await blobs.get("sessions/s1/x.json") is None


@pg_required
async def test_pg_semantic_store_roundtrip():
    from da_semantic.store_pg import PgSemanticStore

    tenant = f"test_{uuid.uuid4().hex[:8]}"
    store = PgSemanticStore(tenant_id=tenant)
    v1 = await store.put("metric", "GMV", {"expr": "SUM(a)"}, "alice")
    v2 = await store.put("metric", "GMV", {"expr": "SUM(a) WHERE s=1"}, "bob")
    assert (v1.version, v2.version) == (1, 2)

    latest = await store.get("metric", "GMV")
    assert latest.version == 2 and latest.payload["expr"].endswith("s=1")
    history = await store.history("metric", "GMV")
    assert [r.version for r in history] == [1, 2]
    assert await store.list_names("metric") == ["GMV"]
    assert await store.get("metric", "不存在") is None


@pg_required
async def test_pg_audit_sink_append_and_query():
    from da_governance import AuditEvent
    from da_governance.audit_pg import PgAuditSink
    from da_types import UserIdentity

    tenant = f"test_{uuid.uuid4().hex[:8]}"
    sink = PgAuditSink()
    ident = UserIdentity(tenant_id=tenant, user_id="u1")
    e = AuditEvent(tenant_id=tenant, session_id="s1", turn_id="t1",
                   stage="question", identity=ident, payload={"text": "q?"})
    await sink.append(e)
    await sink.append(e)  # 幂等：同 event_id 不重复

    rows = await sink.recent(tenant)
    assert len(rows) == 1
    assert rows[0]["stage"] == "question" and rows[0]["payload"]["text"] == "q?"
    by_session = await sink.recent(tenant, session_id="s1")
    assert len(by_session) == 1
