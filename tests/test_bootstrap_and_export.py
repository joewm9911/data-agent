"""冷启动流水线编排（4.2）+ 开放格式导出（4.6）。"""

from datetime import UTC, datetime, timedelta

from da_connectors.sqlite import SQLiteConnector
from da_evals.scenario_cx import seed_database
from da_semantic import (
    ConfirmationQueue,
    EvidenceGraph,
    InMemorySemanticStore,
    bootstrap_semantic_layer,
    export_semantic_layer,
)
from da_types import HistoricalQuery, TimeWindow, UserIdentity

IDENTITY = UserIdentity(tenant_id="t", user_id="u", claims={"allowed_databases": "main"})


class SQLiteWithHistory(SQLiteConnector):
    """SQLite 无 query_log；测试注入合成历史模拟企业数仓（CK 场景开箱即得）。"""

    async def get_query_history(self, window):  # type: ignore[override]
        sqls = [
            "SELECT c.region, SUM(o.order_amt) FROM orders o "
            "JOIN crm_contacts c ON o.cust_no = c.client_code "
            "WHERE o.stat = 1 GROUP BY c.region",
            "SELECT COUNT(*) FROM cs_tickets t "
            "JOIN crm_contacts c ON t.customer_id = c.client_code",
            "SELECT t.cat, COUNT(*) FROM cs_tickets t "
            "JOIN crm_contacts c ON t.customer_id = c.client_code GROUP BY t.cat",
            "SELECT o.chan, SUM(o.order_amt) FROM orders o "
            "JOIN crm_contacts c ON o.cust_no = c.client_code GROUP BY o.chan",
        ]
        for i, sql in enumerate(sqls):
            yield HistoricalQuery(
                query_id=str(i), sql=sql, started_at=datetime.now(UTC)
            )


async def test_bootstrap_first_day(tmp_path):
    """第 1 天 SOP：接入 → 语义层草稿 + 指标草稿 + 确认队列，当天可用。"""
    db = tmp_path / "cx.db"
    seed_database(db)
    connector = SQLiteWithHistory("cx", db)
    store = InMemorySemanticStore()
    graph = EvidenceGraph()
    queue = ConfirmationQueue(store, graph)
    window = TimeWindow(start=datetime.now(UTC) - timedelta(days=90), end=datetime.now(UTC))

    report = await bootstrap_semantic_layer(
        connector, store, queue, IDENTITY, window
    )

    assert report.mining.parsed_queries == 4
    assert report.entities_created, "应产出至少一个实体草稿"
    # 客户实体三绑定齐全
    entity = (await store.get("entity", report.entities_created[0])).payload
    bound = {(b["table"], b["column"]) for b in entity["bindings"]}
    assert {("orders", "cust_no"), ("crm_contacts", "client_code"),
            ("cs_tickets", "customer_id")} <= bound
    assert report.metrics_drafted, "应产出指标草稿"


async def test_export_open_format():
    store = InMemorySemanticStore()
    await store.put(
        "entity", "客户",
        {"name": "客户", "canonical_key": "cid", "aliases": ["会员"],
         "bindings": [{"table": "orders", "column": "cust_no", "grain": ""}],
         "join_paths": [{"expr": "a.x = b.y", "evidence": "human", "confidence": 1.0}],
         "enum_mappings": [], "semantic_roles": []},
        "ops",
    )
    await store.put(
        "metric", "GMV",
        {"name": "GMV", "definition": "口径", "expr": "SUM(amt)",
         "grain": ["day"], "verified": True, "restricted": False},
        "ops",
    )
    exported = await export_semantic_layer(store)
    layer = exported["semantic_layer"]
    assert layer["entities"][0]["aliases"] == ["会员"]
    assert layer["metrics"][0]["meta"]["verified"] is True
    assert layer["metrics"][0]["expr"] == "SUM(amt)"
