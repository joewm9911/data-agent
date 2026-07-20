"""确认队列（4.4）+ 学习回路（4.5）。"""

from da_semantic import (
    ConfirmationQueue,
    EvidenceGraph,
    InMemorySemanticStore,
    LearningLoop,
    Metric,
)
from da_semantic.evidence import EvidenceEdge


async def test_confirmation_entity_merge_yes_and_no():
    store = InMemorySemanticStore()
    graph = EvidenceGraph()
    queue = ConfirmationQueue(store, graph)

    edge = EvidenceEdge(
        left=("orders", "cust_no"), right=("crm_contacts", "client_code"),
        kind="value_overlap", score=0.7, detail="值域包含度 85%",
    )
    item_yes = queue.add_entity_merge(edge, priority=10)
    item_no = queue.add_entity_merge(
        EvidenceEdge(left=("a", "x"), right=("b", "y"), kind="value_overlap",
                     score=0.6, detail="值域包含度 80%"),
        priority=1,
    )
    # 幂律排序：高优先级在前
    assert queue.pending()[0].item_id == item_yes.item_id

    await queue.answer(item_yes.item_id, "是，同一实体", actor="dba")
    await queue.answer(item_no.item_id, "否，不同实体", actor="dba")

    # 确认→人工判决边（1.0），归一后自动合并
    assert graph.pair_confidence(("orders", "cust_no"), ("crm_contacts", "client_code")) >= 0.99
    # 否决→反例，永不再合并
    result = graph.unify()
    flat = {tuple(m) for c in result.auto_clusters for m in c.members}
    assert ("a", "x") not in flat
    assert not queue.pending()


async def test_confirmation_metric_caliber_writes_back():
    store = InMemorySemanticStore()
    await store.put(
        "metric", "GMV",
        Metric(name="GMV", definition="草稿", expr="SUM(amt)", verified=False).model_dump(),
        "bootstrap",
    )
    queue = ConfirmationQueue(store)
    item = queue.add_metric_caliber(
        "GMV", ["SUM(amt) WHERE stat=1", "SUM(amt)"], priority=100
    )
    await queue.answer(item.item_id, "SUM(amt) WHERE stat=1", actor="cfo")

    record = await store.get("metric", "GMV")
    assert record.payload["expr"] == "SUM(amt) WHERE stat=1"
    assert record.payload["verified"] is True
    assert record.version == 2  # 版本化：历史保留


async def test_learning_loop_full_cycle():
    store = InMemorySemanticStore()
    loop = LearningLoop(store)
    await store.put(
        "entity", "客户",
        {"name": "客户", "canonical_key": "customer_id", "aliases": ["会员"],
         "bindings": [], "join_paths": [], "enum_mappings": [], "semantic_roles": []},
        "bootstrap",
    )
    await store.put(
        "metric", "GMV",
        Metric(name="GMV", definition="d", expr="SUM(amt)", verified=True).model_dump(),
        "bootstrap",
    )

    # 澄清即沉淀：同一问题只问一次
    await loop.record_clarification("买家", "客户", actor="ops")
    record = await store.get("entity", "客户")
    assert "买家" in record.payload["aliases"]

    # 纠正即训练：新版本 + 反例
    await loop.record_correction(
        "GMV", wrong_expr="SUM(amt)", corrected_expr="SUM(amt) WHERE stat=1",
        reason="应排除未支付", actor="cfo",
    )
    record = await store.get("metric", "GMV")
    assert "stat=1" in record.payload["expr"]
    counters = await store.list_names("counter_example")
    assert len(counters) == 1

    # 冲突显式化：两个 verified 版本 expr 互异 → 暴露
    conflicts = await loop.detect_conflicts()
    assert len(conflicts) == 1
    assert conflicts[0].metric_name == "GMV"

    # verified answer 生长与命中
    await loop.record_verified_answer("6月GMV", "SELECT SUM(amt) ...", actor="cfo")
    hit = await loop.find_verified_answer("6月GMV")
    assert hit is not None and hit.verified_by == "cfo"
    assert await loop.find_verified_answer("7月GMV") is None
