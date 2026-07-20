"""数据分析 agent loop（M1 问答切片）。

流程：语义上下文注入 → LLM 循环（run_sql 工具）→ 护栏执行 → 错误回流自纠错 → 四件套回答。
全链路审计：question / generation / guard / execution / presentation 六阶段落审计链。
"""

from __future__ import annotations

import uuid
from typing import Any

from da_connectors.base import Connector, ConnectorError, GuardRejectedError
from da_governance import AuditEvent, AuditSink, referenced_objects
from da_semantic import SemanticStore
from da_types import (
    CatalogSnapshot,
    DataObject,
    GuardPolicy,
    MetadataScope,
    Query,
    UserIdentity,
)
from pydantic import BaseModel, Field

from da_agent.context import build_system_prompt
from da_agent.llm import LLMClient

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


class DataAnalystAgent:
    def __init__(
        self,
        connector: Connector,
        semantic_store: SemanticStore,
        audit_sink: AuditSink,
        llm: LLMClient,
        guard: GuardPolicy | None = None,
    ) -> None:
        self._connector = connector
        self._semantics = semantic_store
        self.audit_sink = audit_sink
        self._llm = llm
        self._guard = guard or GuardPolicy()
        self._catalog: CatalogSnapshot | None = None

    async def _get_catalog(self) -> CatalogSnapshot:
        if self._catalog is None:
            self._catalog = await self._connector.get_metadata(MetadataScope())
        return self._catalog

    async def ask(
        self, question: str, identity: UserIdentity, session_id: str | None = None
    ) -> Answer:
        session_id = session_id or uuid.uuid4().hex
        turn_id = uuid.uuid4().hex

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

        await audit("question", {"text": question})

        catalog = await self._get_catalog()
        system = await build_system_prompt(
            self._connector, self._semantics, catalog, identity
        )
        messages: list[dict[str, Any]] = [{"role": "user", "content": question}]
        executed: list[ExecutedSQL] = []

        for step in range(1, MAX_STEPS + 1):
            response = await self._llm.create(system, messages, tools=[RUN_SQL_TOOL])
            tool_calls = [b for b in response.content if b.type == "tool_use"]

            if not tool_calls:
                text = "".join(b.text for b in response.content if b.type == "text")
                await audit("presentation", {"text": text, "steps": step})
                return Answer(
                    question=question,
                    text=text,
                    executed=executed,
                    steps=step,
                    session_id=session_id,
                    turn_id=turn_id,
                )

            messages.append({"role": "assistant", "content": response.content})
            tool_results = []
            for call in tool_calls:
                statement = call.input.get("statement", "")
                purpose = call.input.get("purpose", "")
                await audit("generation", {"statement": statement, "purpose": purpose})
                record, result_text = await self._run_sql(statement, purpose, identity, audit)
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

        text = "分析步骤超出上限，已中止。已执行的查询见审计记录。"
        await audit("presentation", {"text": text, "steps": MAX_STEPS, "aborted": True})
        return Answer(
            question=question,
            text=text,
            executed=executed,
            steps=MAX_STEPS,
            session_id=session_id,
            turn_id=turn_id,
        )

    async def _run_sql(
        self, statement: str, purpose: str, identity: UserIdentity, audit
    ) -> tuple[ExecutedSQL, str]:
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
                return record, "没有找到相关数据。请确认所需数据在你的可用范围内。"
        try:
            result = await self._connector.execute(
                Query(statement=statement, dialect=self._connector.dialect),
                identity,
                self._guard,
            )
        except GuardRejectedError as e:
            await audit("guard", {"statement": statement, "allowed": False, "reason": str(e)})
            record = ExecutedSQL(statement=statement, purpose=purpose, ok=False, error=str(e))
            return record, f"查询被安全护栏拒绝：{e}。请改用只读的单条 SELECT。"
        except ConnectorError as e:
            await audit("execution", {"statement": statement, "ok": False, "error": str(e)})
            record = ExecutedSQL(statement=statement, purpose=purpose, ok=False, error=str(e))
            return record, f"查询执行失败：{e}。请检查表名/列名并修正 SQL。"

        await audit(
            "guard", {"statement": statement, "allowed": True}
        )
        await audit(
            "execution",
            {"statement": statement, "ok": True, "rows": len(result.rows)},
        )
        header = ",".join(c.name for c in result.columns)
        body = "\n".join(",".join(str(v) for v in row) for row in result.rows)
        text = f"{header}\n{body}"
        if len(text) > MAX_RESULT_CHARS:
            text = text[:MAX_RESULT_CHARS] + f"\n...（已截断，共 {len(result.rows)} 行）"
        if result.truncated:
            text += "\n（注意：结果被行数上限截断，聚合请在 SQL 内完成）"
        record = ExecutedSQL(
            statement=statement, purpose=purpose, ok=True, row_count=len(result.rows)
        )
        return record, text
