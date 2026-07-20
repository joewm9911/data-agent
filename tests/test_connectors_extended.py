"""Connector 扩展：conformance 套件（3.1）、dbt 导入（3.2）、MCP 桥（3.3）。"""

from datetime import UTC, datetime

from da_connectors.conformance import run_conformance
from da_connectors.importers import import_dbt_manifest
from da_connectors.mcp_bridge import McpConnector
from da_connectors.sqlite import SQLiteConnector
from da_evals.scenario_cx import seed_database
from da_types import (
    DataObject,
    GuardPolicy,
    MetadataScope,
    Query,
    TimeWindow,
    UserIdentity,
)

ALLOWED = UserIdentity(tenant_id="t", user_id="ok", claims={"allowed_databases": "main"})
DENIED = UserIdentity(tenant_id="t", user_id="no", claims={})


async def test_sqlite_passes_conformance(tmp_path):
    db = tmp_path / "cx.db"
    seed_database(db)
    result = await run_conformance(SQLiteConnector("cx", db), ALLOWED, DENIED)
    assert not result.failures, result.failures
    assert len(result.passed) >= 7


def test_dbt_manifest_import():
    manifest = {
        "nodes": {
            "model.proj.orders": {
                "resource_type": "model",
                "name": "orders",
                "alias": "orders",
                "description": "订单事实表，一行一订单",
                "columns": {
                    "cust_no": {"description": "客户编号，关联 crm_contacts.client_code"},
                    "order_amt": {"description": "订单金额（含税）"},
                },
            },
            "seed.proj.x": {"resource_type": "seed", "name": "x"},
        },
        "metrics": {
            "metric.proj.gmv": {
                "name": "gmv",
                "label": "GMV",
                "description": "已支付订单金额",
                "expr": "SUM(order_amt)",
                "dimensions": ["chan"],
            }
        },
    }
    imported = import_dbt_manifest(manifest)
    assert imported.table_docs["orders"].startswith("订单事实表")
    assert ("orders", "cust_no") in imported.column_docs
    assert imported.metric_drafts["GMV"]["expr"] == "SUM(order_amt)"
    assert imported.metric_drafts["GMV"]["grain"] == ["chan"]


async def test_mcp_bridge_roundtrip(tmp_path):
    """MCP 桥：企业侧把内部系统包成四方法端点，桥适配为标准 Connector。"""
    db = tmp_path / "cx.db"
    seed_database(db)
    backend = SQLiteConnector("backend", db)

    async def transport(method: str, params: dict) -> dict:
        # 模拟企业平台团队的 MCP server：转发到内部真实系统
        if method == "execute":
            result = await backend.execute(
                Query.model_validate(params["query"]),
                UserIdentity.model_validate(params["identity"]),
                GuardPolicy.model_validate(params["guard"]),
            )
            return result.model_dump()
        if method == "get_metadata":
            snap = await backend.get_metadata(MetadataScope.model_validate(params["scope"]))
            return snap.model_dump(mode="json")
        if method == "get_query_history":
            return {"queries": []}
        if method == "check_access":
            decision = await backend.check_access(
                UserIdentity.model_validate(params["identity"]),
                [DataObject.model_validate(o) for o in params["objects"]],
            )
            return decision.model_dump()
        raise ValueError(method)

    bridge = McpConnector("via-mcp", transport, dialect="sqlite")

    catalog = await bridge.get_metadata(MetadataScope())
    assert {t.name for t in catalog.tables} == {"orders", "crm_contacts", "cs_tickets"}

    result = await bridge.execute(
        Query(statement="SELECT COUNT(*) FROM orders", dialect="sqlite"),
        ALLOWED,
        GuardPolicy(),
    )
    assert result.rows[0][0] == 3000

    decision = await bridge.check_access(
        DENIED, [DataObject(database="main", table="orders")]
    )
    assert not decision.all_allowed()

    window = TimeWindow(start=datetime(2026, 1, 1, tzinfo=UTC), end=datetime.now(UTC))
    assert [q async for q in bridge.get_query_history(window)] == []
