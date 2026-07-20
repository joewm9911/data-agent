"""运营端：概览统计 / 集成测试体检 / 审计筛选 / 用户权限视图 / 控制台页面。"""

from da_api import create_app
from da_connectors.dataset import DatasetStore
from da_evals.scenario_cx import seed_database
from da_governance import AuditEvent
from da_types import UserIdentity
from fastapi.testclient import TestClient
from test_api import FakeAgent
from test_api_streaming_auth import make_state

H = {"X-User-Id": "admin", "X-Tenant-Id": "acme"}


def make_console_client(tmp_path):
    db = tmp_path / "cx.db"
    seed_database(db)
    state = make_state()
    state.datasets = DatasetStore(tmp_path / "ds.db")
    state.agent_factory = lambda conn: FakeAgent(state.agent._semantics)
    for tenant, user, stage in [("acme", "alice", "question"),
                                ("acme", "alice", "execution"),
                                ("other", "bob", "question")]:
        state.audit.events.append(
            AuditEvent(tenant_id=tenant, session_id=f"s-{tenant}", turn_id="t",
                       stage=stage,
                       identity=UserIdentity(tenant_id=tenant, user_id=user),
                       payload={"text": "q", "statement": "SELECT 1"})
        )
    return TestClient(create_app(state)), state, db


def test_console_page_served(tmp_path):
    c, _, _ = make_console_client(tmp_path)
    r = c.get("/console")
    assert r.status_code == 200
    assert "运营控制台" in r.text and "集成测试" in r.text


def test_overview_tenant_scoped(tmp_path):
    c, state, _ = make_console_client(tmp_path)
    o = c.get("/admin/overview?tenant=acme", headers=H).json()
    assert o["audit_events"] == 2 and o["users"] == 1 and o["sessions"] == 1
    o_all = c.get("/admin/overview", headers=H).json()
    assert o_all["audit_events"] == 3 and o_all["users"] == 2


def test_audit_filters(tmp_path):
    c, _, _ = make_console_client(tmp_path)
    assert len(c.get("/admin/audit?tenant=acme", headers=H).json()) == 2
    assert len(c.get("/admin/audit?tenant=acme&stage=execution",
                     headers=H).json()) == 1
    assert len(c.get("/admin/audit?session=s-other", headers=H).json()) == 1


def test_source_integration_healthcheck(tmp_path):
    """集成测试体检：连通性 + 只读护栏两项，全过才 passed。"""
    c, state, db = make_console_client(tmp_path)
    c.post("/admin/sources", headers=H, json={
        "source_id": "cx", "kind": "sqlite", "config": {"path": str(db)}})
    r = c.post("/admin/sources/cx/test", headers=H).json()
    assert r["passed"] is True
    names = {chk["name"]: chk["ok"] for chk in r["checks"]}
    assert names == {"连通性/元数据": True, "只读护栏": True}

    r404 = c.post("/admin/sources/nope/test", headers=H)
    assert r404.status_code == 404


def test_users_permission_view(tmp_path):
    c, state, _ = make_console_client(tmp_path)
    c.put("/admin/users/alice/permissions", headers=H,
          json={"allowed_databases": "main"})
    users = {u["user_id"]: u for u in c.get("/admin/users", headers=H).json()}
    assert users["alice"]["allowed_databases"] == "main"
    assert users["alice"]["tenant"] == "acme"
    assert users["bob"]["allowed_databases"] == ""  # 出现过但未授权 → 运营端可见
