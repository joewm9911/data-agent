"""指标直连（三层匹配的确定性层）：名称/别名判决 + 定义提名 + agent 口径注入。"""

from da_semantic import InMemorySemanticStore, LearningLoop, Metric, MetricResolver


async def seed(store):
    await store.put("metric", "GMV", Metric(
        name="GMV", definition="已支付订单金额汇总，排除测试账号",
        expr="SUM(order_amt) WHERE stat=1", aliases=["成交额", "销售额"],
        verified=True).model_dump(), "ops")
    await store.put("metric", "工单量", Metric(
        name="工单量", definition="客服工单数量统计",
        expr="COUNT(*) FROM cs_tickets", verified=True).model_dump(), "ops")
    await store.put("metric", "薪酬总额", Metric(
        name="薪酬总额", definition="员工薪酬", expr="SUM(salary)",
        restricted=True).model_dump(), "ops")


async def test_name_and_alias_exact_match():
    store = InMemorySemanticStore()
    await seed(store)
    resolver = MetricResolver(store)

    by_name = await resolver.resolve("2026年6月的GMV是多少？")
    assert by_name[0].metric.name == "GMV"
    assert by_name[0].score == 1.0 and by_name[0].matched_by == "name"

    by_alias = await resolver.resolve("上个月成交额多少")
    assert by_alias[0].metric.name == "GMV" and by_alias[0].matched_by == "alias"


async def test_definition_ngram_nomination_and_no_match():
    store = InMemorySemanticStore()
    await seed(store)
    resolver = MetricResolver(store)

    nominated = await resolver.resolve("客服工单数量统计一下")
    assert nominated and nominated[0].metric.name == "工单量"

    assert await resolver.resolve("天气怎么样") == []  # 不相关 → LLM 兜底


async def test_alias_clarification_flywheel():
    """澄清即沉淀：登记别名后，原本不命中的问法变为判决性命中。"""
    store = InMemorySemanticStore()
    await seed(store)
    resolver = MetricResolver(store)
    assert not [m for m in await resolver.resolve("流水是多少") if m.score >= 0.999]

    await LearningLoop(store).record_metric_alias("流水", "GMV", actor="ops")
    hit = await resolver.resolve("流水是多少")
    assert hit[0].metric.name == "GMV" and hit[0].score == 1.0


async def test_agent_injects_caliber_hint_and_skips_restricted(tmp_path):
    """判决性命中 → 口径提示注入用户消息；受限指标不注入（6.2-3）。"""
    from da_agent import DataAnalystAgent
    from da_connectors.sqlite import SQLiteConnector
    from da_evals.scenario_cx import seed_database
    from da_governance import InMemoryAuditSink
    from da_types import UserIdentity

    class FakeBlock:
        type = "text"
        text = "好的"

        def model_dump(self, **kw):
            return {"type": "text", "text": self.text}

    class FakeLLM:
        def __init__(self):
            self.seen_messages = None

        async def create(self, system, messages, tools=None, on_token=None, usage=None):
            self.seen_messages = messages
            from types import SimpleNamespace

            return SimpleNamespace(content=[FakeBlock()],
                                   usage=SimpleNamespace(input_tokens=1, output_tokens=1))

    db = tmp_path / "cx.db"
    seed_database(db)
    store = InMemorySemanticStore()
    await seed(store)
    llm = FakeLLM()
    agent = DataAnalystAgent(
        connector=SQLiteConnector("cx", db), semantic_store=store,
        audit_sink=InMemoryAuditSink(), llm=llm,  # type: ignore[arg-type]
    )
    identity = UserIdentity(tenant_id="t", user_id="u",
                            claims={"allowed_databases": "main"})

    answer = await agent.ask("6月成交额是多少", identity)
    user_msg = llm.seen_messages[0]["content"]
    assert "[系统提示]" in user_msg and "GMV" in user_msg and "排除测试账号" in user_msg
    assert answer.matched_metrics == ["GMV"]
    # 命中进审计（8.1：语义层成熟度可观测）
    q_event = next(e for e in agent.audit_sink.events if e.stage == "question")
    assert q_event.payload["matched_metrics"][0]["name"] == "GMV"

    # 受限指标：即使名称命中也不注入口径（元数据即敏感信息）
    answer2 = await agent.ask("薪酬总额是多少", identity)
    assert answer2.matched_metrics == []
    assert "[系统提示]" not in llm.seen_messages[0]["content"]
