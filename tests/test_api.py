"""交付层 API（9）：对话回合 / 报告 / 管理控制台。确定性 FakeAgent，无 LLM。"""

from datetime import UTC, datetime

import pytest
from da_agent.agent import Answer, ExecutedSQL
from da_api import AppState, create_app
from da_evals import EvalCase, judge
from da_evals.harness import EvalReport
from da_governance import AuditEvent, InMemoryAuditSink
from da_runtime import SessionController
from da_semantic import ConfirmationQueue, EvidenceGraph, InMemorySemanticStore
from da_semantic.evidence import EvidenceEdge
from da_types import UserIdentity
from fastapi.testclient import TestClient


class FakeAgent:
    """duck-typed DataAnalystAgent：确定性回答，带语义 store 供导出端点。"""

    def __init__(self, store):
        self._semantics = store
        self.audit_sink = InMemoryAuditSink()
        self.calls = 0

    async def ask(self, question, identity, session_id=None, history=None, on_token=None):
        self.calls += 1
        n = sum(1 for m in (history or []) if m.get("role") == "user") + 1
        if on_token is not None:
            for ch in "流式":
                await on_token(ch)
        return Answer(
            question=question,
            text=f"[{identity.user_id}] 第{n}问的答案",
            executed=[ExecutedSQL(statement="SELECT 1", ok=True, row_count=1)],
            steps=1,
            session_id=session_id or "s",
            turn_id=f"turn{self.calls}",
            transcript=(history or [])
            + [{"role": "user", "content": question},
               {"role": "assistant", "content": "ok"}],
        )


@pytest.fixture()
def client():
    store = InMemorySemanticStore()
    agent = FakeAgent(store)
    graph = EvidenceGraph()
    queue = ConfirmationQueue(store, graph)
    queue.add_entity_merge(
        EvidenceEdge(left=("orders", "cust_no"), right=("crm", "client_code"),
                     kind="value_overlap", score=0.7, detail="85%"),
        priority=5,
    )
    state = AppState(
        agent=agent,  # type: ignore[arg-type]
        controller=None,  # type: ignore[arg-type]
        audit=agent.audit_sink,
        confirmations=queue,
    )
    state.controller = SessionController(executor=state.make_executor())
    er = EvalReport(ran_at=datetime.now(UTC))
    case = EvalCase(case_id="g1", question="q", expected=["答案"])
    er.results.append(judge(case, "答案"))
    state.eval_report = er
    return TestClient(create_app(state)), state


def test_turn_flow_and_continuity(client):
    c, state = client
    headers = {"X-User-Id": "alice", "X-Tenant-Id": "acme"}
    r1 = c.post("/sessions/s1/turns", json={"question": "6月GMV?"}, headers=headers)
    assert r1.status_code == 200
    assert "第1问" in r1.json()["answer"]

    r2 = c.post("/sessions/s1/turns", json={"question": "那7月呢?"}, headers=headers)
    assert "第2问" in r2.json()["answer"]  # 会话连续性经 runtime 转录传递

    # 会话归属校验
    r3 = c.post("/sessions/s1/turns", json={"question": "x"},
                headers={"X-User-Id": "bob", "X-Tenant-Id": "acme"})
    assert r3.status_code == 403


def test_report_flow(client):
    c, _ = client
    headers = {"X-User-Id": "alice", "X-Tenant-Id": "acme"}
    c.post("/sessions/s9/turns", json={"question": "GMV?"}, headers=headers)
    r = c.post("/reports/from-session/s9", headers=headers)
    report_id = r.json()["report_id"]
    got = c.get(f"/reports/{report_id}")
    assert got.status_code == 200
    assert "第1问的答案" in got.json()["markdown"]


def test_admin_permissions_and_confirmations(client):
    c, state = client
    r = c.put("/admin/users/alice/permissions",
              json={"allowed_databases": "main,sales"})
    assert r.json()["allowed_databases"] == "main,sales"
    assert state.permissions["alice"] == "main,sales"

    items = c.get("/admin/confirmations").json()
    assert len(items) == 1 and items[0]["kind"] == "entity_merge"
    item_id = items[0]["item_id"]
    r = c.post(f"/admin/confirmations/{item_id}/answer",
               json={"choice": "是，同一实体"}, headers={"X-User-Id": "dba"})
    assert r.json()["status"] == "answered"
    assert c.get("/admin/confirmations").json() == []


def test_admin_audit_and_dashboard(client):
    c, state = client
    ident = UserIdentity(tenant_id="acme", user_id="alice")
    state.audit.events.append(
        AuditEvent(tenant_id="acme", session_id="s", turn_id="t",
                   stage="question", identity=ident, payload={"text": "q"})
    )
    events = c.get("/admin/audit").json()
    assert events and events[-1]["stage"] == "question"

    dash = c.get("/admin/eval-dashboard").json()
    assert dash["accuracy"] == 1.0
    assert "准确率仪表盘" in dash["markdown"]
