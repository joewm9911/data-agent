"""会话运行时（7.2/7.3/D2/D4）：确定性执行体，无 LLM。

覆盖：回合串行、转录连续性、休眠→水合恢复、fencing 快照、主动回合同链路。
"""

from datetime import UTC, datetime, timedelta

from da_runtime import SessionController, SessionMeta, Turn
from da_types import UserIdentity

IDENTITY = UserIdentity(tenant_id="t", user_id="u")


def make_executor():
    """确定性执行体：回答 = 已见历史条数（验证连续性）。"""

    async def executor(question, identity, session_id, history):
        transcript = list(history)
        transcript.append({"role": "user", "content": question})
        answer = f"回合#{sum(1 for m in transcript if m['role'] == 'user')}：{question}"
        transcript.append({"role": "assistant", "content": answer})
        return answer, transcript

    return executor


async def test_turn_continuity_within_session():
    controller = SessionController(executor=make_executor())
    meta = SessionMeta(session_id="s1", tenant_id="t", user_id="u")
    runtime = await controller.ensure(meta)

    await runtime.enqueue(Turn(session_id="s1", input_text="6月GMV?"))
    await runtime.enqueue(Turn(session_id="s1", input_text="那7月呢?"))

    o1 = await runtime.run_one_turn(IDENTITY)
    o2 = await runtime.run_one_turn(IDENTITY)
    assert o1.answer_text.startswith("回合#1")
    assert o2.answer_text.startswith("回合#2")  # 历史被带上（连续性）
    assert o1.turn.status == "completed"
    assert len(runtime.transcript) == 4


async def test_hibernate_and_hydrate_preserves_history():
    """IDLE→快照销毁→COLD→新"机器"水合：文件内容跟会话走（D4）。"""
    controller = SessionController(executor=make_executor(), idle_seconds=0.0)
    meta = SessionMeta(session_id="s2", tenant_id="t", user_id="u")
    runtime = await controller.ensure(meta)
    await runtime.enqueue(Turn(session_id="s2", input_text="第一问"))
    await runtime.run_one_turn(IDENTITY)

    evicted = await controller.idle_sweep(datetime.now(UTC) + timedelta(seconds=1))
    assert evicted == ["s2"]
    assert controller.active_sessions() == []
    assert meta.state == "cold"

    # 重新活跃：水合出与销毁前一致的转录，继续计数
    runtime2 = await controller.ensure(meta, worker_id="worker-2")
    assert len(runtime2.transcript) == 2
    await runtime2.enqueue(Turn(session_id="s2", input_text="第二问"))
    outcome = await runtime2.run_one_turn(IDENTITY)
    assert outcome.answer_text.startswith("回合#2")


async def test_proactive_turn_same_pipeline():
    """主动任务 = 无用户消息的回合，同队列同链路（7.4）。"""
    controller = SessionController(executor=make_executor())
    meta = SessionMeta(session_id="s3", tenant_id="t", user_id="system")
    runtime = await controller.ensure(meta)
    await runtime.enqueue(
        Turn(session_id="s3", kind="proactive", input_text="[晨报] 工单量异常检查")
    )
    outcome = await runtime.run_one_turn(IDENTITY)
    assert outcome is not None and outcome.turn.kind == "proactive"
    assert outcome.turn.status == "completed"


async def test_failed_turn_does_not_kill_runtime():
    async def bad_executor(question, identity, session_id, history):
        raise RuntimeError("boom")

    controller = SessionController(executor=bad_executor)
    meta = SessionMeta(session_id="s4", tenant_id="t", user_id="u")
    runtime = await controller.ensure(meta)
    await runtime.enqueue(Turn(session_id="s4", input_text="x"))
    outcome = await runtime.run_one_turn(IDENTITY)
    assert outcome.turn.status == "failed"
    assert "boom" in outcome.error
    # 运行时仍可继续处理后续回合
    assert runtime.meta.state == "active"
