"""交付层 API（架构文档 9）。

- 对话：POST /sessions/{sid}/turns（sync 返回四件套；stream=true 时后台执行 + SSE）
- 流式：GET /sessions/{sid}/stream（SSE 订阅 pub/sub 频道——执行者与连接持有者解耦，D4）
- 报告：POST /reports/from-session/{sid}、GET /reports/{id}
- 管理控制台：权限（6.3）/确认队列（4.4）/审计（8.1）/准确率仪表盘（8.3）/语义导出（4.6）
- 身份：设置 identity_provider 后要求 Authorization: Bearer <token>（10.2 IdentityProvider）；
  未设置时退化为 X-User-Id 头（开发模式）。
- Web：GET / 返回单页前端（对话 + 管理台）。
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field

from da_agent import DataAnalystAgent, render_answer_report
from da_agent.report import Report
from da_evals import EvalReport
from da_governance import InMemoryAuditSink
from da_platform.identity import IdentityProvider
from da_platform.memory import InMemoryPubSub
from da_runtime import SessionController, SessionMeta, Turn
from da_semantic import ConfirmationQueue, export_semantic_layer
from da_types import UserIdentity
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel

from da_api.web import INDEX_HTML


@dataclass
class AppState:
    agent: DataAnalystAgent
    controller: SessionController
    audit: InMemoryAuditSink
    confirmations: ConfirmationQueue
    pubsub: InMemoryPubSub = field(default_factory=InMemoryPubSub)
    identity_provider: IdentityProvider | None = None
    permissions: dict[str, str] = field(default_factory=dict)  # user_id -> allowed_databases
    reports: dict[str, Report] = field(default_factory=dict)
    sessions: dict[str, SessionMeta] = field(default_factory=dict)
    last_answers: dict[str, object] = field(default_factory=dict)  # session_id -> Answer
    eval_report: EvalReport | None = None

    def apply_permissions(self, identity: UserIdentity) -> UserIdentity:
        if identity.user_id in self.permissions:
            claims = dict(identity.claims)
            claims["allowed_databases"] = self.permissions[identity.user_id]
            return identity.model_copy(update={"claims": claims})
        return identity

    def make_executor(self):
        """标准回合执行体：agent.ask 桥接 runtime；token 经 pub/sub 外发（流式解耦）。"""

        async def executor(question, identity, session_id, history):
            channel = f"session:{session_id}"

            async def on_token(text: str) -> None:
                await self.pubsub.publish(
                    channel, json.dumps({"type": "token", "text": text}).encode()
                )

            answer = await self.agent.ask(
                question, identity, session_id=session_id, history=history,
                on_token=on_token,
            )
            self.last_answers[session_id] = answer
            await self.pubsub.publish(
                channel,
                json.dumps({"type": "done", "text": answer.text,
                            "turn_id": answer.turn_id}, ensure_ascii=False).encode(),
            )
            return answer.text, answer.transcript

        return executor


class TurnRequest(BaseModel):
    question: str
    stream: bool = False


class PermissionRequest(BaseModel):
    allowed_databases: str


class ConfirmAnswerRequest(BaseModel):
    choice: str


def create_app(state: AppState) -> FastAPI:
    app = FastAPI(title="data-agent", version="0.2.0")

    async def get_identity(
        request: Request,
        x_user_id: str | None = Header(default=None, alias="X-User-Id"),
        x_tenant_id: str = Header(default="default", alias="X-Tenant-Id"),
    ) -> UserIdentity:
        if state.identity_provider is not None:
            auth = request.headers.get("Authorization", "")
            if not auth.startswith("Bearer "):
                raise HTTPException(401, "missing bearer token")
            identity = await state.identity_provider.authenticate(auth[7:])
            if identity is None:
                raise HTTPException(401, "invalid token")
            return state.apply_permissions(identity)
        # 开发模式：X-User-Id 头，或 ?uid= 查询参数（SSE EventSource 无法设头）
        user_id = x_user_id or request.query_params.get("uid")
        if not user_id:
            raise HTTPException(401, "missing X-User-Id")
        return state.apply_permissions(
            UserIdentity(tenant_id=x_tenant_id, user_id=user_id)
        )

    @app.get("/", response_class=HTMLResponse)
    async def index():
        return INDEX_HTML

    @app.post("/sessions/{sid}/turns")
    async def create_turn(
        sid: str, body: TurnRequest, identity: UserIdentity = Depends(get_identity)
    ):
        meta = state.sessions.setdefault(
            sid,
            SessionMeta(session_id=sid, tenant_id=identity.tenant_id,
                        user_id=identity.user_id),
        )
        if meta.user_id != identity.user_id:
            raise HTTPException(403, "session belongs to another user")
        runtime = await state.controller.ensure(meta)
        await runtime.enqueue(Turn(session_id=sid, input_text=body.question))

        if body.stream:
            # 后台执行；客户端经 GET /sessions/{sid}/stream 观看（关页面任务照跑）
            asyncio.get_running_loop().create_task(
                runtime.run_one_turn(identity, timeout_seconds=5.0)
            )
            return {"session_id": sid, "stream": f"/sessions/{sid}/stream"}

        outcome = await runtime.run_one_turn(identity, timeout_seconds=5.0)
        if outcome is None or outcome.error:
            raise HTTPException(500, outcome.error if outcome else "turn not executed")
        return {
            "session_id": sid,
            "turn_id": outcome.turn.turn_id,
            "answer": outcome.answer_text,
        }

    @app.get("/sessions/{sid}/stream")
    async def stream_session(sid: str, identity: UserIdentity = Depends(get_identity)):
        meta = state.sessions.get(sid)
        if meta is not None and meta.user_id != identity.user_id:
            raise HTTPException(403, "session belongs to another user")

        async def events():
            async for raw in state.pubsub.subscribe(f"session:{sid}"):
                payload = raw.decode()
                yield f"data: {payload}\n\n"
                if json.loads(payload).get("type") == "done":
                    break

        return StreamingResponse(events(), media_type="text/event-stream")

    @app.post("/reports/from-session/{sid}")
    async def create_report(sid: str, identity: UserIdentity = Depends(get_identity)):
        answer = state.last_answers.get(sid)
        if answer is None:
            raise HTTPException(404, "no answer for session")
        report = render_answer_report(answer, author=identity.user_id)  # type: ignore[arg-type]
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
            {"item_id": i.item_id, "kind": i.kind, "question": i.question,
             "options": i.options, "priority": i.priority}
            for i in state.confirmations.pending()
        ]

    @app.post("/admin/confirmations/{item_id}/answer")
    async def answer_confirmation(
        item_id: str, body: ConfirmAnswerRequest,
        identity: UserIdentity = Depends(get_identity),
    ):
        try:
            item = await state.confirmations.answer(item_id, body.choice, identity.user_id)
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
        return {"markdown": state.eval_report.dashboard_markdown(),
                "accuracy": state.eval_report.accuracy}

    @app.get("/admin/semantic/export")
    async def semantic_export():
        return await export_semantic_layer(state.agent._semantics)  # noqa: SLF001

    return app
