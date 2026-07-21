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
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from da_agent import DataAnalystAgent, render_answer_report
from da_agent.report import Report
from da_connectors.base import Connector, ConnectorError
from da_connectors.dataset import DatasetStore
from da_evals import EvalReport
from da_governance import InMemoryAuditSink
from da_platform.identity import IdentityProvider
from da_platform.memory import InMemoryPubSub
from da_runtime import SessionController, SessionMeta, Turn
from da_semantic import (
    ConfirmationQueue,
    EvidenceGraph,
    bootstrap_semantic_layer,
    export_semantic_layer,
)
from da_types import MetadataScope, TimeWindow, UserIdentity
from fastapi import Depends, FastAPI, Header, HTTPException, Request, UploadFile
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
    # ---- 数据接入产品化（3.2）----
    sources: dict[str, Connector] = field(default_factory=dict)
    active_source: str = ""
    # 换源时重建 agent（保留语义层/审计/LLM，由装配方注入）
    agent_factory: Callable[[Connector], DataAnalystAgent] | None = None
    datasets: DatasetStore | None = None

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


class SourceRequest(BaseModel):
    source_id: str
    kind: str  # sqlite | clickhouse | hive
    config: dict = {}


class IntegrateRequest(BaseModel):
    tables: list[str] = []


class TrialRequest(BaseModel):
    metric: dict
    start: str = ""
    end: str = ""


class BindingPreviewRequest(BaseModel):
    table: str
    expr: str
    source_column: str = ""  # 可选：预览时并排展示原值 → 转换值


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

    @app.get("/console", response_class=HTMLResponse)
    async def console():
        from da_api.console import CONSOLE_HTML

        return CONSOLE_HTML

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
        answer = state.last_answers.get(sid)
        return {
            "session_id": sid,
            "turn_id": outcome.turn.turn_id,
            "answer": outcome.answer_text,
            # 试一问（动线 C）需要的验证信息：指标直连命中即口径生效证明
            "matched_metrics": getattr(answer, "matched_metrics", []),
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
    async def list_audit(
        limit: int = 50,
        tenant: str = "",
        stage: str = "",
        session: str = "",
    ):
        """审计查询：按租户/阶段/会话筛选（8.1 运营端视图）。"""
        events = state.audit.events
        if tenant:
            events = [e for e in events if e.tenant_id == tenant]
        if stage:
            events = [e for e in events if e.stage == stage]
        if session:
            events = [e for e in events if e.session_id == session]
        return [e.model_dump(mode="json") for e in events[-limit:]]

    @app.get("/admin/overview")
    async def overview(tenant: str = ""):
        """运营概览：租户维度的关键统计。"""
        events = state.audit.events
        if tenant:
            events = [e for e in events if e.tenant_id == tenant]
        semantics = state.agent._semantics  # noqa: SLF001
        return {
            "tenant": tenant or "全部",
            "sources": len(state.sources),
            "active_source": state.active_source,
            "entities": len(await semantics.list_names("entity")),
            "metrics": len(await semantics.list_names("metric")),
            "verified_answers": len(await semantics.list_names("verified_answer")),
            "pending_confirmations": len(state.confirmations.pending()),
            "audit_events": len(events),
            "sessions": len({e.session_id for e in events}),
            "users": len({e.identity.user_id for e in events}),
            "accuracy": state.eval_report.accuracy if state.eval_report else None,
        }

    @app.get("/admin/todos")
    async def todos():
        """治理收件箱（动线 B）：待办 = 数量 + 原因 + 直达目标。"""
        from da_semantic import Entity, Metric

        semantics = state.agent._semantics  # noqa: SLF001
        entities, frozen, affected_metrics = [], [], []
        for n in await semantics.list_names("entity"):
            r = await semantics.get("entity", n)
            if r:
                e = Entity.model_validate(r.payload)
                entities.append(e)
                frozen.extend(f"{n}: {fb}" for fb in e.frozen_bindings)
        drafts = []
        for n in await semantics.list_names("metric"):
            r = await semantics.get("metric", n)
            if r:
                m = Metric.model_validate(r.payload)
                if not m.verified:
                    drafts.append(m.name)
                if frozen and m.numerator and any(
                    m.numerator.table in fb for fb in frozen
                ):
                    affected_metrics.append(m.name)
        pending = state.confirmations.pending()
        return {
            "confirmations": {
                "count": len(pending),
                "top": pending[0].question[:60] if pending else "",
            },
            "drift": {"count": len(frozen), "items": frozen[:5],
                      "affected_metrics": affected_metrics},
            "draft_metrics": {"count": len(drafts), "names": drafts[:8]},
            "sop": {
                "has_source": bool(state.sources),
                "active_source": state.active_source,
                "has_semantics": bool(await semantics.list_names("entity"))
                or bool(await semantics.list_names("metric")),
                "confirm_clear": not pending,
            },
        }

    @app.get("/admin/users")
    async def list_users():
        """权限视图：已授权用户 + 审计中出现过的用户。"""
        seen = {e.identity.user_id: e.tenant_id for e in state.audit.events}
        out = {}
        for uid, dbs in state.permissions.items():
            out[uid] = {"user_id": uid, "allowed_databases": dbs,
                        "tenant": seen.get(uid, "")}
        for uid, tenant in seen.items():
            out.setdefault(uid, {"user_id": uid, "allowed_databases": "",
                                 "tenant": tenant})
        return sorted(out.values(), key=lambda u: u["user_id"])

    @app.post("/admin/sources/{source_id}/test")
    async def test_source(source_id: str):
        """接入集成测试：连通性 + 元数据 + 只读护栏三项体检。"""
        connector = state.sources.get(source_id)
        if connector is None:
            raise HTTPException(404, "source not found")
        checks = []
        try:
            catalog = await connector.get_metadata(MetadataScope())
            checks.append({"name": "连通性/元数据", "ok": True,
                           "detail": f"{len(catalog.tables)} 张表"})
        except Exception as e:  # noqa: BLE001
            checks.append({"name": "连通性/元数据", "ok": False, "detail": str(e)})
            return {"source_id": source_id, "passed": False, "checks": checks}
        # 只读护栏体检：写语句必须被拒
        from da_connectors.base import GuardRejectedError
        from da_types import GuardPolicy, Query

        table = catalog.tables[0].name if catalog.tables else "t"
        try:
            await connector.execute(
                Query(statement=f"DELETE FROM {table}", dialect=connector.dialect),
                UserIdentity(tenant_id="_system", user_id="_healthcheck"),
                GuardPolicy(),
            )
            checks.append({"name": "只读护栏", "ok": False, "detail": "写语句未被拦截！"})
        except GuardRejectedError:
            checks.append({"name": "只读护栏", "ok": True, "detail": "写语句被正确拒绝"})
        except Exception as e:  # noqa: BLE001
            checks.append({"name": "只读护栏", "ok": False, "detail": str(e)})
        passed = all(c["ok"] for c in checks)
        return {"source_id": source_id, "passed": passed, "checks": checks}

    @app.get("/admin/eval-dashboard")
    async def eval_dashboard():
        if state.eval_report is None:
            return {"markdown": "尚未运行 eval", "accuracy": None}
        return {"markdown": state.eval_report.dashboard_markdown(),
                "accuracy": state.eval_report.accuracy}

    @app.get("/admin/semantic/export")
    async def semantic_export():
        return await export_semantic_layer(state.agent._semantics)  # noqa: SLF001

    # ---- 数据接入产品化（3.2：快速接入 SOP 的 API 化）----

    async def _test_source(connector: Connector) -> dict:
        """连接测试：拉元数据，返回表数（接入第一步的即时反馈）。"""
        catalog = await connector.get_metadata(MetadataScope())
        return {"tables": len(catalog.tables),
                "table_names": [t.name for t in catalog.tables][:20]}

    @app.get("/admin/sources")
    async def list_sources():
        return [
            {"source_id": sid, "kind": type(c).__name__,
             "active": sid == state.active_source}
            for sid, c in state.sources.items()
        ]

    @app.post("/admin/sources")
    async def add_source(body: SourceRequest):
        if body.kind == "sqlite":
            from da_connectors.sqlite import SQLiteConnector

            connector: Connector = SQLiteConnector(body.source_id, body.config["path"])
        elif body.kind == "clickhouse":
            from da_connectors.clickhouse import ClickHouseConnector

            ck_kwargs = dict(body.config)
            connector = ClickHouseConnector(
                body.source_id, credentials_resolver=lambda identity: ck_kwargs
            )
        elif body.kind == "hive":
            from da_connectors.hive import HiveConnector

            hive_kwargs = dict(body.config)
            connector = HiveConnector(
                body.source_id,
                credentials_resolver=lambda identity: hive_kwargs,
                database=body.config.get("database", "default"),
            )
        else:
            raise HTTPException(400, f"不支持的数据源类型: {body.kind}")

        try:
            test = await _test_source(connector)
        except (ConnectorError, Exception) as e:  # noqa: BLE001 - 连接失败要可读回显
            raise HTTPException(400, f"连接测试失败: {e}") from e
        state.sources[body.source_id] = connector
        return {"source_id": body.source_id, "test": test}

    @app.post("/admin/sources/{source_id}/activate")
    async def activate_source(source_id: str):
        connector = state.sources.get(source_id)
        if connector is None:
            raise HTTPException(404, "source not found")
        if state.agent_factory is None:
            raise HTTPException(500, "agent_factory 未配置")
        state.agent = state.agent_factory(connector)
        state.active_source = source_id
        return {"active_source": source_id}

    async def _run_bootstrap(
        source_id: str, identity: UserIdentity, tables: list[str] | None
    ) -> dict:
        connector = state.sources.get(source_id)
        if connector is None:
            raise HTTPException(404, "source not found")
        graph = EvidenceGraph()
        queue = ConfirmationQueue(state.agent._semantics, graph)  # noqa: SLF001
        # 保留旧队列中未处理的确认项（集成是增量动作，不清空待办）
        for item in state.confirmations.pending():
            queue._items[item.item_id] = item  # noqa: SLF001
        state.confirmations = queue
        window = TimeWindow(
            start=datetime.now(UTC) - timedelta(days=180), end=datetime.now(UTC)
        )
        boot_identity = identity.model_copy(
            update={"claims": {**identity.claims, "allowed_databases": "main,default"}}
        )
        report = await bootstrap_semantic_layer(
            connector, state.agent._semantics, queue, boot_identity, window,  # noqa: SLF001
            scope=MetadataScope(tables=tables or []),
        )
        return {
            "queries_mined": report.mining.parsed_queries,
            "entities_created": report.entities_created,
            "metrics_drafted": report.metrics_drafted,
            "confirmations_queued": report.confirmations_queued,
            "profiled_columns": len(report.profiles),
        }

    @app.post("/admin/sources/{source_id}/bootstrap")
    async def bootstrap_source(
        source_id: str, identity: UserIdentity = Depends(get_identity)
    ):
        """一键冷启动（第 1 天 SOP，4.2）：全库挖掘+profiling → 语义草稿+确认队列。"""
        return await _run_bootstrap(source_id, identity, tables=None)

    @app.get("/admin/sources/{source_id}/metadata")
    async def source_metadata(source_id: str):
        """元数据浏览器：拉取表/列结构供运营端勾选集成。"""
        connector = state.sources.get(source_id)
        if connector is None:
            raise HTTPException(404, "source not found")
        catalog = await connector.get_metadata(MetadataScope())
        return {
            "source_id": source_id,
            "tables": [
                {"name": t.name, "database": t.database, "row_count": t.row_count,
                 "columns": [{"name": c.name, "type": c.type, "comment": c.comment}
                             for c in t.columns]}
                for t in catalog.tables
            ],
        }

    @app.post("/admin/sources/{source_id}/integrate")
    async def integrate_source(
        source_id: str, body: IntegrateRequest,
        identity: UserIdentity = Depends(get_identity),
    ):
        """快速数据集成：勾选表 → profiling+证据图归一 → 语义层草稿+确认队列（4.2/4.3）。"""
        if not body.tables:
            raise HTTPException(400, "至少选择一张表")
        return await _run_bootstrap(source_id, identity, tables=body.tables)

    # ---- 语义层管理（运营端 CRUD，全部版本化写入）----

    @app.get("/admin/semantic/objects")
    async def semantic_objects(kind: str = "metric"):
        semantics = state.agent._semantics  # noqa: SLF001
        out = []
        for name in await semantics.list_names(kind):  # type: ignore[arg-type]
            record = await semantics.get(kind, name)  # type: ignore[arg-type]
            if record is not None:
                out.append({"name": name, "version": record.version,
                            "updated_by": record.updated_by,
                            "updated_at": str(record.updated_at),
                            "payload": record.payload})
        return out

    @app.get("/admin/semantic/history")
    async def semantic_history(kind: str, name: str):
        semantics = state.agent._semantics  # noqa: SLF001
        return [
            {"version": r.version, "updated_by": r.updated_by,
             "updated_at": str(r.updated_at), "payload": r.payload}
            for r in await semantics.history(kind, name)  # type: ignore[arg-type]
        ]

    async def _load_entities():
        from da_semantic import Entity

        semantics = state.agent._semantics  # noqa: SLF001
        out = []
        for n in await semantics.list_names("entity"):
            record = await semantics.get("entity", n)
            if record is not None:
                out.append(Entity.model_validate(record.payload))
        return out

    @app.put("/admin/semantic/metrics/{name}")
    async def put_metric(
        name: str, body: dict, identity: UserIdentity = Depends(get_identity)
    ):
        from da_governance import validate_expression_fragment
        from da_semantic import Metric, validate_metric

        try:
            metric = Metric.model_validate({**body, "name": name})
        except Exception as e:  # noqa: BLE001
            raise HTTPException(400, f"指标定义不合法: {e}") from e

        # 八要素校验：组件完整性 + 时间口径一致性（跨表两表都要有语义角色绑定）
        errors = validate_metric(metric, await _load_entities())
        dialect = state.agent._connector.dialect  # noqa: SLF001
        for label, fragment in [
            ("分子", metric.numerator.expr if metric.numerator else ""),
            ("分母", metric.denominator.expr if metric.denominator else ""),
            ("filter", metric.numerator.filter if metric.numerator else ""),
        ]:
            if fragment.strip():
                err = validate_expression_fragment(fragment, dialect)
                if err:
                    errors.append(f"{label}：{err}")
        if errors:
            raise HTTPException(400, "；".join(errors))

        record = await state.agent._semantics.put(  # noqa: SLF001
            "metric", name, metric.model_dump(), identity.user_id
        )
        return {"name": name, "version": record.version}

    @app.post("/admin/semantic/metrics/trial")
    async def trial_metric_endpoint(
        body: TrialRequest, identity: UserIdentity = Depends(get_identity)
    ):
        """指标试算（保存前强制）：真实执行分子/分母 SQL，返回值与比率。"""
        from da_semantic import Metric, trial_metric

        try:
            metric = Metric.model_validate(body.metric)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(400, f"指标定义不合法: {e}") from e
        try:
            trial = await trial_metric(
                state.agent._connector, metric, await _load_entities(),  # noqa: SLF001
                UserIdentity(tenant_id=identity.tenant_id, user_id="_trial"),
                body.start, body.end,
            )
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        except ConnectorError as e:
            raise HTTPException(400, f"试算执行失败: {e}") from e
        return {
            "numerator_sql": trial.numerator_sql,
            "numerator_value": trial.numerator_value,
            "denominator_sql": trial.denominator_sql,
            "denominator_value": trial.denominator_value,
            "ratio": trial.ratio,
        }

    @app.post("/admin/semantic/bindings/preview")
    async def preview_binding(
        body: BindingPreviewRequest, identity: UserIdentity = Depends(get_identity)
    ):
        """SQL 转换绑定：方言校验 + 采样预览（保存前看到转换结果）。"""
        from da_governance import validate_expression_fragment
        from da_types import GuardPolicy, Query

        connector = state.agent._connector  # noqa: SLF001
        err = validate_expression_fragment(body.expr, connector.dialect)
        if err:
            raise HTTPException(400, err)
        try:
            result = await connector.execute(
                Query(
                    statement=(
                        f"SELECT {body.source_column}, {body.expr} "
                        f"FROM {body.table} LIMIT 5"
                    ) if body.source_column else
                    f"SELECT {body.expr} FROM {body.table} LIMIT 5",
                    dialect=connector.dialect,
                ),
                UserIdentity(tenant_id=identity.tenant_id, user_id="_preview"),
                GuardPolicy(max_result_rows=5),
            )
        except ConnectorError as e:
            raise HTTPException(400, f"预览执行失败: {e}") from e
        return {"ok": True, "rows": result.rows}

    @app.put("/admin/semantic/entities/{name}")
    async def put_entity(
        name: str, body: dict, identity: UserIdentity = Depends(get_identity)
    ):
        from da_semantic import Entity

        try:
            entity = Entity.model_validate({**body, "name": name})
        except Exception as e:  # noqa: BLE001
            raise HTTPException(400, f"实体定义不合法: {e}") from e
        record = await state.agent._semantics.put(  # noqa: SLF001
            "entity", name, entity.model_dump(), identity.user_id
        )
        return {"name": name, "version": record.version}

    @app.post("/admin/datasets/upload")
    async def upload_dataset(file: UploadFile):
        """上传即问（零门槛档）：CSV/TSV/Excel → 数据集库表。"""
        if state.datasets is None:
            raise HTTPException(500, "数据集存储未配置")
        content = await file.read()
        name = (file.filename or "dataset").rsplit(".", 1)
        table = name[0]
        suffix = name[1].lower() if len(name) > 1 else "csv"
        try:
            if suffix in ("xlsx", "xlsm"):
                result = state.datasets.ingest_excel(content, table)
            else:
                result = state.datasets.ingest_csv(content, table)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        # 数据集源自动注册；首次上传即激活（TTFV：上传→提问一条链）
        connector = state.datasets.connector("datasets")
        state.sources["datasets"] = connector
        if state.agent_factory is not None and state.active_source in ("", "datasets"):
            state.agent = state.agent_factory(connector)
            state.active_source = "datasets"
        return {"table": result.table, "columns": result.columns, "rows": result.rows,
                "source_id": "datasets", "activated": state.active_source == "datasets"}

    return app
