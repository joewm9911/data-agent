"""CK 适配器测试：纯函数部分无需真实服务；执行路径用 fake client 验证契约行为。"""

from datetime import datetime

import pytest
from da_connectors import GuardRejectedError, get_connector_cls
from da_connectors.clickhouse.adapter import (
    build_query_log_query,
    build_tables_query,
    guard_settings,
)
from da_types import DataObject, GuardPolicy, MetadataScope, Query, TimeWindow, UserIdentity

IDENTITY = UserIdentity(
    tenant_id="t1", user_id="u1", claims={"allowed_databases": "sales,cx"}
)


class FakeResult:
    def __init__(self, rows):
        self.result_rows = rows
        self.column_names = ["id"]
        self.column_types = ["UInt64"]


class FakeClient:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.executed = []

    def query(self, sql, settings=None):
        self.executed.append((sql, settings))
        return FakeResult([[1], [2]])


def make_connector():
    cls = get_connector_cls("clickhouse")
    clients = []

    def factory(**kwargs):
        c = FakeClient(**kwargs)
        clients.append(c)
        return c

    conn = cls(
        source_id="ck-test",
        credentials_resolver=lambda identity: {"user": identity.user_id},
        client_factory=factory,
    )
    return conn, clients


def test_tables_query_scoped():
    sql = build_tables_query(MetadataScope(databases=["sales"]))
    assert "system.tables" in sql
    assert "'sales'" in sql
    assert "'system'" in sql  # 排除系统库


def test_query_log_query_window():
    w = TimeWindow(start=datetime(2026, 1, 1), end=datetime(2026, 7, 1))
    sql = build_query_log_query(w)
    assert "system.query_log" in sql
    assert "QueryFinish" in sql
    assert "2026-01-01" in sql


def test_guard_settings_hard_limits():
    s = guard_settings(GuardPolicy(max_result_rows=500, max_execution_seconds=30))
    assert s["readonly"] == 1
    assert s["max_result_rows"] == 500
    assert s["max_execution_time"] == 30


async def test_execute_runs_rewritten_statement_as_user():
    conn, clients = make_connector()
    result = await conn.execute(
        Query(statement="SELECT id FROM sales.orders"), IDENTITY, GuardPolicy(max_result_rows=100)
    )
    assert result.rows == [[1], [2]]
    # 以用户身份连接（铁律 P3）
    assert clients[0].kwargs == {"user": "u1"}
    executed_sql, settings = clients[0].executed[0]
    assert "LIMIT 100" in executed_sql  # 执行的是护栏改写后的语句
    assert settings["readonly"] == 1


async def test_execute_rejects_write():
    conn, _ = make_connector()
    with pytest.raises(GuardRejectedError):
        await conn.execute(
            Query(statement="DROP TABLE sales.orders"), IDENTITY, GuardPolicy()
        )


async def test_check_access_allowlist():
    conn, _ = make_connector()
    decision = await conn.check_access(
        IDENTITY,
        [
            DataObject(database="sales", table="orders"),
            DataObject(database="hr", table="salary"),
        ],
    )
    allowed = {o.qualified_name() for o in decision.allowed_objects()}
    assert allowed == {"sales.orders"}
