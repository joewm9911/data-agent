"""数据接入产品化：Hive 适配器 / 数据集上传即问 / 数据源管理 API。"""

import pytest
from da_connectors import GuardRejectedError
from da_connectors.dataset import DatasetStore
from da_connectors.hive import HiveConnector
from da_types import GuardPolicy, MetadataScope, Query, UserIdentity
from fastapi.testclient import TestClient
from test_api import FakeAgent
from test_api_streaming_auth import make_state

IDENTITY = UserIdentity(tenant_id="t", user_id="u", claims={"allowed_databases": "default"})

# ---- Hive 适配器（fake client，无需真实集群）----


class FakeHiveCursor:
    def __init__(self, script):
        self._script = script
        self._rows = []
        self.description = None
        self.executed = []

    def execute(self, sql):
        self.executed.append(sql)
        for prefix, (desc, rows) in self._script.items():
            if sql.strip().upper().startswith(prefix):
                self.description = desc
                self._rows = rows
                return
        self.description = [("col", "string")]
        self._rows = []

    def fetchall(self):
        return self._rows

    def close(self):
        pass


class FakeHiveConn:
    def __init__(self, script):
        self._script = script

    def cursor(self):
        return FakeHiveCursor(self._script)


def make_hive():
    script = {
        "SHOW TABLES": ([("tab_name", "string")], [("orders",), ("users",)]),
        "DESCRIBE ORDERS": (None, [("id", "bigint", "订单ID"), ("amt", "double", "金额"),
                                   ("", "", ""), ("# Partition Information", "", "")]),
        "DESCRIBE USERS": (None, [("uid", "string", "")]),
        "SELECT": ([("orders.cnt", "bigint")], [(42,)]),
    }
    return HiveConnector(
        "hive-test",
        credentials_resolver=lambda identity: {"host": "h", "username": identity.user_id},
        client_factory=lambda **kw: FakeHiveConn(script),
    )


async def test_hive_metadata_parses_describe():
    catalog = await make_hive().get_metadata(MetadataScope())
    tables = {t.name: t for t in catalog.tables}
    assert set(tables) == {"orders", "users"}
    orders = tables["orders"]
    assert [c.name for c in orders.columns] == ["id", "amt"]  # 分区段被截断
    assert orders.columns[1].comment == "金额"


async def test_hive_execute_guarded():
    hive = make_hive()
    result = await hive.execute(
        Query(statement="SELECT COUNT(*) AS cnt FROM orders", dialect="hive"),
        IDENTITY, GuardPolicy(max_result_rows=100),
    )
    assert result.rows == [[42]]
    assert result.columns[0].name == "cnt"  # orders.cnt 前缀被剥离

    with pytest.raises(GuardRejectedError):
        await hive.execute(
            Query(statement="DROP TABLE orders", dialect="hive"), IDENTITY, GuardPolicy()
        )


async def test_hive_empty_history():
    from datetime import UTC, datetime, timedelta

    from da_types import TimeWindow

    window = TimeWindow(start=datetime.now(UTC) - timedelta(days=1),
                        end=datetime.now(UTC))
    assert [q async for q in make_hive().get_query_history(window)] == []


# ---- 数据集：上传即问 ----


async def test_dataset_csv_roundtrip(tmp_path):
    store = DatasetStore(tmp_path / "ds.db")
    csv_content = "月份,GMV,订单数\n2026-05,100.5,10\n2026-06,200.75,20\n"
    result = store.ingest_csv(csv_content, "月度销售")
    assert result.rows == 2 and result.columns == ["月份", "GMV", "订单数"]

    connector = store.connector()
    qr = await connector.execute(
        Query(statement='SELECT SUM(GMV) FROM 月度销售', dialect="sqlite"),
        UserIdentity(tenant_id="t", user_id="u"),
        GuardPolicy(max_result_rows=10),
    )
    assert qr.rows[0][0] == pytest.approx(301.25)  # 类型推断 REAL 生效


def test_dataset_type_inference_and_dedupe(tmp_path):
    store = DatasetStore(tmp_path / "ds.db")
    r = store.ingest_csv("a,a,b!c\n1,x,2.5\n3,y,\n", "t1")
    assert r.columns == ["a", "a_1", "b_c"]  # 重名去重 + 特殊字符清洗

    import sqlite3

    conn = sqlite3.connect(tmp_path / "ds.db")
    types = {row[1]: row[2] for row in conn.execute("PRAGMA table_info(t1)")}
    assert types == {"a": "INTEGER", "a_1": "TEXT", "b_c": "REAL"}


def test_dataset_rejects_empty(tmp_path):
    store = DatasetStore(tmp_path / "ds.db")
    with pytest.raises(ValueError, match="为空"):
        store.ingest_csv("only_header\n", "t")


# ---- 数据源管理 API ----


@pytest.fixture()
def onboarding_client(tmp_path):
    from da_evals.scenario_cx import seed_database

    db = tmp_path / "cx.db"
    seed_database(db)
    state = make_state()
    state.datasets = DatasetStore(tmp_path / "datasets.db")
    state.agent_factory = lambda conn: FakeAgent(state.agent._semantics)
    from da_api import create_app

    return TestClient(create_app(state)), state, db


def test_source_add_activate_bootstrap(onboarding_client):
    c, state, db = onboarding_client
    headers = {"X-User-Id": "admin"}

    # 添加：连接测试通过并返回表数
    r = c.post("/admin/sources", headers=headers, json={
        "source_id": "cx", "kind": "sqlite", "config": {"path": str(db)}})
    assert r.status_code == 200 and r.json()["test"]["tables"] == 3

    # 坏路径：连接测试失败要可读回显
    r = c.post("/admin/sources", headers=headers, json={
        "source_id": "bad", "kind": "sqlite", "config": {"path": "/no/such.db"}})
    assert r.status_code == 400 and "连接测试失败" in r.json()["detail"]

    # 激活：换源重建 agent
    r = c.post("/admin/sources/cx/activate", headers=headers)
    assert r.json()["active_source"] == "cx"
    assert [s for s in c.get("/admin/sources", headers=headers).json()
            if s["source_id"] == "cx"][0]["active"]

    # 一键冷启动：profiling 出草稿（SQLite 无 query_log，靠信号三）
    r = c.post("/admin/sources/cx/bootstrap", headers=headers)
    body = r.json()
    assert r.status_code == 200
    assert body["profiled_columns"] > 0


def test_dataset_upload_end_to_end(onboarding_client):
    c, state, _ = onboarding_client
    csv_bytes = "city,revenue\n北京,100\n上海,200\n".encode()
    r = c.post(
        "/admin/datasets/upload",
        headers={"X-User-Id": "admin"},
        files={"file": ("q3销售.csv", csv_bytes, "text/csv")},
    )
    body = r.json()
    assert r.status_code == 200
    assert body["table"] == "q3销售" and body["rows"] == 2
    assert body["activated"] is True  # 首上传即激活：上传→提问一条链
    assert "datasets" in state.sources
    assert state.datasets.list_tables() == ["q3销售"]
