"""交付层 API（架构文档 9）。

- 对话：POST /sessions/{sid}/turns（回合入队并执行，返回四件套回答）
- 报告：POST /reports/from-turn、GET /reports/{id}
- 管理控制台（第二产品，数据负责人视角）：
  - 权限：PUT /admin/users/{uid}/permissions（渐进授权 6.3）
  - 确认队列：GET /admin/confirmations、POST /admin/confirmations/{id}/answer（4.4）
  - 审计：GET /admin/audit（8.1）
  - 准确率仪表盘：GET /admin/eval-dashboard（8.3）
  - 语义层导出：GET /admin/semantic/export（4.6）
- 身份：M2 用 X-User-Id/X-Tenant-Id 头模拟，生产替换为 SSO 中间件（IdentityProvider）
"""

from __future__ import annotations

from dataclasses import dataclass, field

from da_agent import DataAnalystAgent, render_answer_report
from da_agent.report import Report
from da_evals import EvalReport
from da_governance import InMemoryAuditSink
from da_runtime import SessionController, SessionMeta, Turn
from da_semantic import ConfirmationQueue, export_semantic_layer
from da_types import UserIdentity
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel


@dataclass
class AppState:
    agent: DataAnalystAgent
    controller: SessionController
    audit: InMemoryAuditSink
    confirmations: ConfirmationQueue
    permissions: dict[str, str] = field(default_factory=dict)  # user_id -> allowed_databases
    reports: dict[str, Report] = field(default_factory=dict)
    sessions: dict[str, SessionMeta] = field(default_factory=dict)
    last_answers: dict[str, object] = field(default_factory=dict)  # session_id -> Answer
    eval_report: EvalReport | None = None

    def make_executor(self):
        """标准回合执行体：agent.ask 桥接 runtime.TurnExecutor，回答留存供报告生成。"""

        async def executor(question, identity, session_id, history):
            answer = await self.agent.ask(
                question, identity, session_id=session_id, history=history
            )
            self.last_answers[session_id] = answer
            return answer.text, answer.transcript

        return executor


class TurnRequest(BaseModel):
    question: str


class PermissionRequest(BaseModel):
    allowed_databases: str  # 逗号分隔


class ConfirmAnswerRequest(BaseModel):
    choice: str


def create_app(state: AppState) -> FastAPI:
    app = FastAPI(title="data-agent", version="0.1.0")

    def identity_from(user_id: str, tenant_id: str) -> UserIdentity:
        claims = {}
        if user_id in state.permissions:
            claims["allowed_databases"] = state.permissions[user_id]
        return UserIdentity(tenant_id=tenant_id, user_id=user_id, claims=claims)

    @app.post("/sessions/{sid}/turns")
    async def create_turn(
        sid: str,
        body: TurnRequest,
        x_user_id: str = Header(alias="X-User-Id"),
        x_tenant_id: str = Header(alias="X-Tenant-Id", default="default"),
    ):
        identity = identity_from(x_user_id, x_tenant_id)
        meta = state.sessions.setdefault(
            sid, SessionMeta(session_id=sid, tenant_id=x_tenant_id, user_id=x_user_id)
        )
        if meta.user_id != x_user_id:
            raise HTTPException(403, "session belongs to another user")
        runtime = await state.controller.ensure(meta)
        await runtime.enqueue(Turn(session_id=sid, input_text=body.question))
        outcome = await runtime.run_one_turn(identity, timeout_seconds=1.0)
        if outcome is None or outcome.error:
            raise HTTPException(500, outcome.error if outcome else "turn not executed")
        return {
            "session_id": sid,
            "turn_id": outcome.turn.turn_id,
            "answer": outcome.answer_text,
        }

    @app.post("/reports/from-session/{sid}")
    async def create_report(sid: str, x_user_id: str = Header(alias="X-User-Id")):
        answer = state.last_answers.get(sid)
        if answer is None:
            raise HTTPException(404, "no answer for session")
        report = render_answer_report(answer, author=x_user_id)  # type: ignore[arg-type]
        state.reports[report.report_id] = report
        return {"report_id": report.report_id}

    @app.get("/reports/{report_id}")
    async def get_report(report_id: str):
        report = state.reports.get(report_id)
        if report is None:
            raise HTTPException(404, "report not found")
        return report.model_dump(mode="json")

    # ---- 管理控制台 ----

    @app.put("/admin/users/{uid}/permissions")
    async def set_permissions(uid: str, body: PermissionRequest):
        state.permissions[uid] = body.allowed_databases
        return {"user_id": uid, "allowed_databases": body.allowed_databases}

    @app.get("/admin/confirmations")
    async def list_confirmations():
        return [
            {
                "item_id": i.item_id,
                "kind": i.kind,
                "question": i.question,
                "options": i.options,
                "priority": i.priority,
            }
            for i in state.confirmations.pending()
        ]

    @app.post("/admin/confirmations/{item_id}/answer")
    async def answer_confirmation(
        item_id: str,
        body: ConfirmAnswerRequest,
        x_user_id: str = Header(alias="X-User-Id"),
    ):
        try:
            item = await state.confirmations.answer(item_id, body.choice, x_user_id)
        except KeyError as e:
            raise HTTPException(404, "confirmation not found") from e
        return {"item_id": item.item_id, "status": item.status}

    @app.get("/admin/audit")
    async def list_audit(limit: int = 50):
        return [e.model_dump(mode="json") for e in state.audit.events[-limit:]]

    @app.get("/admin/eval-dashboard")
    async def eval_dashboard():
        if state.eval_report is None:
            return {"markdown": "尚未运行 eval", "accuracy": None}
        return {
            "markdown": state.eval_report.dashboard_markdown(),
            "accuracy": state.eval_report.accuracy,
        }

    @app.get("/admin/semantic/export")
    async def semantic_export():
        return await export_semantic_layer(state.agent._semantics)  # noqa: SLF001

    return app
