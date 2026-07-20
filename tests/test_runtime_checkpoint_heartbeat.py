"""回合检查点 + 心跳自动续租（7.2）。"""

import asyncio

from da_platform.memory import InMemoryLeaseManager
from da_runtime import SessionController, SessionMeta, Turn
from da_runtime.lock import SessionTurnLock
from da_types import UserIdentity

IDENTITY = UserIdentity(tenant_id="t", user_id="u")


async def test_heartbeat_keeps_lease_during_long_turn():
    """回合执行时间远超租约 ttl，心跳自动续租保证租约不丢。"""

    async def slow_executor(question, identity, session_id, history):
        await asyncio.sleep(0.5)  # 远超 ttl=0.1s
        return "done", list(history) + [{"role": "assistant", "content": "done"}]

    controller = SessionController(executor=slow_executor)
    meta = SessionMeta(session_id="hb1", tenant_id="t", user_id="u")
    runtime = await controller.ensure(meta)
    # 换成超短 ttl 的锁，验证心跳生效
    runtime._lock = SessionTurnLock(controller.leases, ttl_seconds=0.1)  # noqa: SLF001

    await runtime.enqueue(Turn(session_id="hb1", input_text="慢分析"))
    outcome = await runtime.run_one_turn(IDENTITY, timeout_seconds=1.0)
    assert outcome is not None and outcome.turn.status == "completed"
    # 若心跳失效，续租会抛 StaleTokenError 或快照被拒；completed 即证明租约全程持有


async def test_checkpoint_saved_and_cleared():
    checkpoints: list[list] = []

    async def executor(question, identity, session_id, history):
        # 模拟 agent：经 make_checkpointer 写两次检查点（两个工具轮）
        cp = executor.runtime.make_checkpointer("turn-x")
        partial = list(history) + [{"role": "user", "content": question}]
        await cp(partial)
        checkpoints.append(await executor.runtime.load_checkpoint("turn-x"))
        partial.append({"role": "assistant", "content": "中间结果"})
        await cp(partial)
        checkpoints.append(await executor.runtime.load_checkpoint("turn-x"))
        return "ok", partial

    controller = SessionController(executor=executor)
    meta = SessionMeta(session_id="cp1", tenant_id="t", user_id="u")
    runtime = await controller.ensure(meta)
    executor.runtime = runtime

    await runtime.enqueue(Turn(session_id="cp1", input_text="q"))
    outcome = await runtime.run_one_turn(IDENTITY, timeout_seconds=1.0)
    assert outcome.turn.status == "completed"
    assert len(checkpoints[0]) == 1 and len(checkpoints[1]) == 2  # 检查点逐轮增长
    # 回合成功结束后检查点清除（转录已全量快照）
    assert await runtime.load_checkpoint(outcome.turn.turn_id) is None


async def test_lease_manager_used_by_lock_exposes_ttl():
    lock = SessionTurnLock(InMemoryLeaseManager(), ttl_seconds=42.0)
    assert lock.ttl_seconds == 42.0
