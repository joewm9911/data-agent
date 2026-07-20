"""生产启动入口：按环境变量装配 provider（10.2 差异收敛在配置层）。

环境变量：
- DA_CONNECTOR: "sqlite:<path>"（单机演示）或 "clickhouse"（配 DA_CK_* 凭证）
- DA_PG_DSN / DA_REDIS_URL / DA_BLOB_ROOT：缺省时回退内存实现（开发模式）
- DA_LLM_API_KEY / DA_LLM_BASE_URL / DA_LLM_MODEL
"""

from __future__ import annotations

import os

from da_agent import (
    DataAnalystAgent,
    LLMClient,
    LLMConfig,
    PlaybookRegistry,
    channel_review_playbook,
    cx_ticket_anomaly_playbook,
)
from da_agent.config import load_dotenv
from da_connectors.sqlite import SQLiteConnector
from da_governance import InMemoryAuditSink
from da_governance.breaker import CircuitBreaker, RateQuota
from da_runtime import SessionController
from da_semantic import ConfirmationQueue, InMemorySemanticStore
from da_types import GuardPolicy

from da_api.app import AppState, create_app


def _build_state() -> AppState:
    load_dotenv()

    connector_spec = os.environ.get("DA_CONNECTOR", "sqlite:examples/cx.db")
    if connector_spec.startswith("sqlite:"):
        connector = SQLiteConnector("default", connector_spec.split(":", 1)[1])
    elif connector_spec == "clickhouse":
        from da_connectors.clickhouse import ClickHouseConnector

        ck_kwargs = {
            "host": os.environ.get("DA_CK_HOST", "localhost"),
            "port": int(os.environ.get("DA_CK_PORT", "8123")),
            "username": os.environ.get("DA_CK_USER", "default"),
            "password": os.environ.get("DA_CK_PASSWORD", ""),
        }
        connector = ClickHouseConnector(
            "default", credentials_resolver=lambda identity: ck_kwargs
        )
    else:
        raise RuntimeError(f"未知 DA_CONNECTOR: {connector_spec}")

    # 语义层/审计：有 PG 用 PG，否则内存（开发模式）
    if os.environ.get("DA_PG_DSN"):
        from da_governance.audit_pg import PgAuditSink
        from da_semantic.store_pg import PgSemanticStore

        semantic_store = PgSemanticStore()
        audit = PgAuditSink()
    else:
        semantic_store = InMemorySemanticStore()
        audit = InMemoryAuditSink()

    playbooks = PlaybookRegistry()
    playbooks.register(cx_ticket_anomaly_playbook())
    playbooks.register(channel_review_playbook())

    agent = DataAnalystAgent(
        connector=connector,
        semantic_store=semantic_store,
        audit_sink=audit,  # type: ignore[arg-type]
        llm=LLMClient(LLMConfig.from_env()),
        guard=GuardPolicy(max_result_rows=int(os.environ.get("DA_MAX_ROWS", "1000"))),
        playbooks=playbooks,
        breaker=CircuitBreaker(),
        quota=RateQuota(),
    )
    state = AppState(
        agent=agent,
        controller=None,  # type: ignore[arg-type]
        audit=audit,  # type: ignore[arg-type]
        confirmations=ConfirmationQueue(semantic_store),
    )

    # 演示模式：默认 sqlite 场景库时播种语义层 + 授权演示用户（生产配置下不生效）
    if connector_spec == "sqlite:examples/cx.db":
        import asyncio

        from da_evals.scenario_cx import seed_semantics

        asyncio.get_event_loop().run_until_complete(seed_semantics(semantic_store))
        state.permissions["analyst_1"] = "main"

    kwargs = {"executor": state.make_executor()}
    if os.environ.get("DA_REDIS_URL"):
        from da_platform.redis_providers import (
            RedisLeaseManager,
            RedisSessionQueue,
        )

        kwargs["queue"] = RedisSessionQueue()
        kwargs["leases"] = RedisLeaseManager()
    if os.environ.get("DA_BLOB_ROOT"):
        from da_platform.fsblob import FileSystemBlobStore

        kwargs["blobs"] = FileSystemBlobStore(os.environ["DA_BLOB_ROOT"])
    state.controller = SessionController(**kwargs)
    return state


app = create_app(_build_state())
