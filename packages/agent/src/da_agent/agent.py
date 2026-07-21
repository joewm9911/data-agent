"""数据分析 agent loop（分析引擎核心）。

流程：语义上下文注入 → LLM 循环（run_sql / run_attribution 工具）→ 护栏执行 →
错误回流自纠错 → 四件套回答。会话连续性：接受历史消息、返回可持久化转录（7.2）。
全链路审计：question / generation / guard / execution / presentation 落审计链。
"""

from __future__ import annotations

import time
import uuid
from typing import Any

from da_connectors.base import Connector, ConnectorError, GuardRejectedError
from da_governance import AuditEvent, AuditSink, referenced_objects, sanitize_result_text
from da_governance.breaker import (
    CircuitBreaker,
    CircuitOpenError,
    QuotaExceededError,
    RateQuota,
)
from da_semantic import LearningLoop, SemanticStore
from da_semantic.resolver import MetricResolver
from da_types import (
    CatalogSnapshot,
    DataObject,
    GuardPolicy,
    MetadataScope,
    Query,
    UserIdentity,
)
from pydantic import BaseModel, Field

from da_agent.charts import render_chart
from da_agent.context import build_system_prompt
from da_agent.llm import LLMClient, OnToken, UsageCounter
from da_agent.metric_tree import MetricNode, MetricTreeEngine
from da_agent.playbooks import PlaybookEngine, PlaybookRegistry

RUN_SQL_TOOL = {
    "name": "run_sql",
    "description": "对企业数据库执行只读 SQL 查询并返回结果。查询会经过安全护栏。",
    "input_schema": {
        "type": "object",
        "properties": {
            "statement": {"type": "string", "description": "要执行的 SQL（单条 SELECT）"},
            "purpose": {"type": "string", "description": "这条 SQL 想验证/计算什么"},
        },
        "required": ["statement"],
    },
}

ATTRIBUTION_TOOL = {
    "name": "run_attribution",
    "description": (
        "指标归因引擎：对注册过的指标做两期对比+维度分解，返回带贡献度排序的诊断报告。"
        "回答'为什么涨/跌'类问题优先用本工具，不要手写多条 SQL。"
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "metric": {"type": "string", "description": "指标树名称"},
            "base_where": {"type": "string", "description": "基期过滤条件（SQL WHERE 片段）"},
            "current_where": {"type": "string", "description": "当期过滤条件（SQL WHERE 片段）"},
            "base_label": {"type": "string"},
            "current_label": {"type": "string"},
        },
        "required": ["metric", "base_where", "current_where"],
    },
}

PLAYBOOK_TOOL = {
    "name": "run_playbook",
    "description": (
        "执行预置分析套路（playbook）：固定步骤+解读框架，输出质量稳定。"
        "问题匹配某个 playbook 场景时优先使用。"
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "playbook": {"type": "string", "description": "playbook 名称"},
            "params": {"type": "object", "description": "参数（日期等），字符串值"},
        },
        "required": ["playbook"],
    },
}

MAX_STEPS = 10
MAX_RESULT_CHARS = 4000  # 数据最小化（6.2-4）：大结果集截断后回流模型


class ExecutedSQL(BaseModel):
    statement: str
    purpose: str = ""
    ok: bool
    error: str = ""
    row_count: int = 0


class Answer(BaseModel):
    question: str
    text: str
    executed: list[ExecutedSQL] = Field(default_factory=list)
    steps: int = 0
    session_id: str
    turn_id: str
    # 可持久化转录（会话连续性，7.2）：下一回合作为 history 传回
    transcript: list[dict] = Field(default_factory=list)
    # token 成本统计（8.2）
    input_tokens: int = 0
    output_tokens: int = 0
    llm_calls: int = 0
    # 自动生成的 SVG 图表（5.1 四件套之"图"）
    charts: list[str] = Field(default_factory=list)
    # 指标直连命中（确定性匹配，语义层成熟度指标）
    matched_metrics: list[str] = Field(default_factory=list)


class DataAnalystAgent:
    def __init__(
        self,
        connector: Connector,
        semantic_store: SemanticStore,
        audit_sink: AuditSink,
        llm: LLMClient,
        guard: GuardPolicy | None = None,
        metric_trees: dict[str, MetricNode] | None = None,
        playbooks: PlaybookRegistry | None = None,
        learning: LearningLoop | None = None,
        breaker: CircuitBreaker | None = None,
        quota: RateQuota | None = None,
    ) -> None:
        self._connector = connector
        self._semantics = semantic_store
        self.audit_sink = audit_sink
        self._llm = llm
        self._guard = guard or GuardPolicy()
        self._metric_trees = metric_trees or {}
        self._playbooks = playbooks
        self._playbook_engine = PlaybookEngine(connector, self._guard)
        self._learning = learning
        self._breaker = breaker
        self._quota = quota
        self._attribution = MetricTreeEngine(connector, self._guard)
        self._metric_resolver = MetricResolver(semantic_store)
        self._catalog: CatalogSnapshot | None = None

    async def _get_catalog(self) -> CatalogSnapshot:
        if self._catalog is None:
            self._catalog = await self._connector.get_metadata(MetadataScope())
        return self._catalog

    def _tools(self) -> list[dict[str, Any]]:
        tools = [RUN_SQL_TOOL]
        if self._metric_trees:
            tool = dict(ATTRIBUTION_TOOL)
            tool["description"] += f" 已注册指标树：{sorted(self._metric_trees)}"
            tools.append(tool)
        if self._playbooks is not None and self._playbooks.names():
            tool = dict(PLAYBOOK_TOOL)
            specs = [
                f"{n}(参数:{','.join(self._playbooks.get(n).params)})"
                for n in self._playbooks.names()
            ]
            tool["description"] += f" 可用：{specs}"
            tools.append(tool)
        return tools

    async def ask(
        self,
        question: str,
        identity: UserIdentity,
        session_id: str | None = None,
        history: list[dict] | None = None,
        on_token: OnToken | None = None,
        on_checkpoint=None,  # Callable[[list[dict]], Awaitable[None]]：每个工具轮后调用
    ) -> Answer:
        session_id = session_id or uuid.uuid4().hex
        turn_id = uuid.uuid4().hex
        usage = UsageCounter()

        async def audit(stage: str, payload: dict[str, Any]) -> None:
            await self.audit_sink.append(
                AuditEvent(
                    tenant_id=identity.tenant_id,
                    session_id=session_id,
                    turn_id=turn_id,
                    stage=stage,  # type: ignore[arg-type]
                    identity=identity,
                    payload=payload,
                )
            )

        # 指标直连（三层匹配的确定性层）：名称/别名判决性命中 → 口径置顶注入
        metric_matches = await self._metric_resolver.resolve(question)
        exact = [m for m in metric_matches if m.score >= 0.999
                 and not m.metric.restricted]
        await audit(
            "question",
            {"text": question,
             "matched_metrics": [
                 {"name": m.metric.name, "by": m.matched_by, "score": m.score}
                 for m in metric_matches
             ]},
        )

        catalog = await self._get_catalog()
        system = await build_system_prompt(
            self._connector, self._semantics, catalog, identity
        )
        messages: list[dict[str, Any]] = list(history or [])
        executed: list[ExecutedSQL] = []
        charts: list[str] = []

        # verified answer 命中（4.5/6.1）：复用 SQL 模板，以提问者身份重执行
        user_content = question
        if exact:
            hints = "\n".join(
                f"- 指标「{m.metric.name}」"
                f"（{'别名' if m.matched_by == 'alias' else '名称'}命中）："
                f"{m.metric.caliber_text()}"
                for m in exact
            )
            user_content = (
                f"{question}\n\n[系统提示] 问题命中以下预定义指标，"
                f"必须严格按其口径计算，不得自行调整：\n{hints}"
            )
        if self._learning is not None:
            hit = await self._learning.find_verified_answer(question)
            if hit is not None and not hit.restricted:
                record, result_text, _ = await self._run_sql(
                    hit.sql_template, "verified answer 模板重执行", identity, audit
                )
                if record.ok:
                    executed.append(record)
                    user_content = (
                        f"{question}\n\n[系统提示] 该问题命中已验证答案"
                        f"（verified by {hit.verified_by}），已以当前用户身份重新执行模板：\n"
                        f"SQL: {hit.sql_template}\n结果:\n{result_text}\n"
                        f"请基于此结果作答并声明口径；如结果不适用可另行查询。"
                    )
        messages.append({"role": "user", "content": user_content})

        for step in range(1, MAX_STEPS + 1):
            response = await self._llm.create(
                system, messages, tools=self._tools(), on_token=on_token, usage=usage
            )
            tool_calls = [b for b in response.content if b.type == "tool_use"]

            if not tool_calls:
                text = "".join(b.text for b in response.content if b.type == "text")
                await audit(
                    "presentation",
                    {"text": text, "steps": step,
                     "usage": {"input_tokens": usage.input_tokens,
                               "output_tokens": usage.output_tokens,
                               "llm_calls": usage.llm_calls}},
                )
                messages.append({"role": "assistant", "content": response.content})
                return Answer(
                    question=question,
                    text=text,
                    executed=executed,
                    steps=step,
                    session_id=session_id,
                    turn_id=turn_id,
                    transcript=_serialize_messages(messages),
                    input_tokens=usage.input_tokens,
                    output_tokens=usage.output_tokens,
                    llm_calls=usage.llm_calls,
                    charts=charts,
                    matched_metrics=[m.metric.name for m in exact],
                )

            messages.append({"role": "assistant", "content": response.content})
            tool_results = []
            for call in tool_calls:
                if call.name == "run_attribution":
                    record, result_text = await self._run_attribution(
                        call.input, identity, audit
                    )
                elif call.name == "run_playbook":
                    record, result_text = await self._run_playbook(
                        call.input, identity, audit
                    )
                else:
                    statement = call.input.get("statement", "")
                    purpose = call.input.get("purpose", "")
                    await audit(
                        "generation", {"statement": statement, "purpose": purpose}
                    )
                    record, result_text, chart = await self._run_sql(
                        statement, purpose, identity, audit
                    )
                    if chart is not None:
                        charts.append(chart)
                executed.append(record)
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": call.id,
                        "content": result_text,
                        "is_error": not record.ok,
                    }
                )
            messages.append({"role": "user", "content": tool_results})
            if on_checkpoint is not None:
                await on_checkpoint(_serialize_messages(messages))

        text = "分析步骤超出上限，已中止。已执行的查询见审计记录。"
        await audit("presentation", {"text": text, "steps": MAX_STEPS, "aborted": True})
        return Answer(
            question=question,
            text=text,
            executed=executed,
            steps=MAX_STEPS,
            session_id=session_id,
            turn_id=turn_id,
            transcript=_serialize_messages(messages),
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            llm_calls=usage.llm_calls,
            charts=charts,
            matched_metrics=[m.metric.name for m in exact],
        )

    async def _run_attribution(
        self, args: dict[str, Any], identity: UserIdentity, audit
    ) -> tuple[ExecutedSQL, str]:
        metric_name = args.get("metric", "")
        node = self._metric_trees.get(metric_name)
        label = f"attribution:{metric_name}"
        if node is None:
            record = ExecutedSQL(
                statement=label, ok=False, error=f"未注册的指标树: {metric_name}"
            )
            return record, f"未注册的指标树: {metric_name}，可用：{sorted(self._metric_trees)}"
        await audit("generation", {"attribution": args})
        try:
            report = await self._attribution.attribute(
                node,
                base_where=args.get("base_where", "1=1"),
                current_where=args.get("current_where", "1=1"),
                identity=identity,
                base_label=args.get("base_label", "基期"),
                current_label=args.get("current_label", "当期"),
            )
        except ConnectorError as e:
            record = ExecutedSQL(statement=label, ok=False, error=str(e))
            return record, f"归因执行失败：{e}"
        await audit(
            "execution",
            {"attribution": metric_name, "ok": True, "evidence_sql": report.evidence_sql},
        )
        record = ExecutedSQL(
            statement=label, purpose="metric tree attribution", ok=True,
            row_count=len(report.steps),
        )
        return record, report.narrative()

    async def _run_playbook(
        self, args: dict[str, Any], identity: UserIdentity, audit
    ) -> tuple[ExecutedSQL, str]:
        name = args.get("playbook", "")
        label = f"playbook:{name}"
        if self._playbooks is None or name not in self._playbooks.names():
            available = self._playbooks.names() if self._playbooks else []
            record = ExecutedSQL(statement=label, ok=False, error="unknown playbook")
            return record, f"未注册的 playbook: {name}，可用：{available}"
        await audit("generation", {"playbook": name, "params": args.get("params", {})})
        try:
            run = await self._playbook_engine.run(
                self._playbooks.get(name), dict(args.get("params") or {}), identity
            )
        except (ConnectorError, ValueError) as e:
            record = ExecutedSQL(statement=label, ok=False, error=str(e))
            return record, f"playbook 执行失败：{e}"
        await audit("execution", {"playbook": name, "ok": True,
                                  "steps": len(run.results)})
        record = ExecutedSQL(statement=label, purpose="playbook", ok=True,
                             row_count=len(run.results))
        return record, run.narrative()

    async def _run_sql(
        self, statement: str, purpose: str, identity: UserIdentity, audit
    ) -> tuple[ExecutedSQL, str, str | None]:
        # 熔断与配额（3.4）：先于一切执行
        try:
            if self._quota is not None:
                self._quota.check(identity.tenant_id)
            if self._breaker is not None:
                self._breaker.check()
        except (CircuitOpenError, QuotaExceededError) as e:
            await audit("guard", {"statement": statement, "allowed": False, "reason": str(e)})
            record = ExecutedSQL(statement=statement, purpose=purpose, ok=False, error=str(e))
            return record, f"查询被限流保护拒绝：{e}", None

        # 执行前权限判定（6.1）：引用对象逐一 check_access，任一无权即拒绝。
        # 拒绝话术不泄露对象存在性（"没有找到相关数据"而非"你无权访问 X"）。
        objects = referenced_objects(statement, self._connector.dialect)
        if objects:
            decision = await self._connector.check_access(
                identity, [DataObject(database=db, table=t) for db, t in objects]
            )
            if not decision.all_allowed():
                await audit(
                    "guard",
                    {"statement": statement, "allowed": False, "reason": "access denied"},
                )
                record = ExecutedSQL(
                    statement=statement, purpose=purpose, ok=False, error="access denied"
                )
                return record, "没有找到相关数据。请确认所需数据在你的可用范围内。", None

        started = time.monotonic()
        try:
            result = await self._connector.execute(
                Query(statement=statement, dialect=self._connector.dialect),
                identity,
                self._guard,
            )
        except GuardRejectedError as e:
            await audit("guard", {"statement": statement, "allowed": False, "reason": str(e)})
            record = ExecutedSQL(statement=statement, purpose=purpose, ok=False, error=str(e))
            return record, f"查询被安全护栏拒绝：{e}。请改用只读的单条 SELECT。", None
        except ConnectorError as e:
            if self._breaker is not None:
                self._breaker.record(ok=False)
            await audit("execution", {"statement": statement, "ok": False, "error": str(e)})
            record = ExecutedSQL(statement=statement, purpose=purpose, ok=False, error=str(e))
            return record, f"查询执行失败：{e}。请检查表名/列名并修正 SQL。", None

        duration_ms = (time.monotonic() - started) * 1000
        if self._breaker is not None:
            self._breaker.record(ok=True, duration_ms=duration_ms)

        await audit("guard", {"statement": statement, "allowed": True})
        await audit(
            "execution",
            {"statement": statement, "ok": True, "rows": len(result.rows),
             "duration_ms": round(duration_ms, 1)},
        )
        header = ",".join(c.name for c in result.columns)
        body = "\n".join(",".join(str(v) for v in row) for row in result.rows)
        text = sanitize_result_text(f"{header}\n{body}")  # 注入防御（6.2-2）
        if len(text) > MAX_RESULT_CHARS:
            text = text[:MAX_RESULT_CHARS] + f"\n...（已截断，共 {len(result.rows)} 行）"
        if result.truncated:
            text += "\n（注意：结果被行数上限截断，聚合请在 SQL 内完成）"
        record = ExecutedSQL(
            statement=statement, purpose=purpose, ok=True, row_count=len(result.rows)
        )
        chart = render_chart(result, title=purpose or "查询结果")
        return record, text, chart


def _serialize_messages(messages: list[dict[str, Any]]) -> list[dict]:
    """转录序列化：anthropic 内容块 → 纯 dict（可 JSON 持久化，下一回合原样传回）。"""
    out: list[dict] = []
    for m in messages:
        content = m["content"]
        if isinstance(content, str):
            out.append({"role": m["role"], "content": content})
            continue
        blocks = []
        for b in content:
            if isinstance(b, dict):
                blocks.append(b)
            elif hasattr(b, "model_dump"):
                blocks.append(b.model_dump(exclude_none=True))
        out.append({"role": m["role"], "content": blocks})
    return out
