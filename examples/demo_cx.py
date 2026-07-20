"""端到端演示：真实 CX 场景 + MiniMax（Anthropic 兼容端点）。

用法：uv run python examples/demo_cx.py
"""

import asyncio
import sys
from pathlib import Path

from da_agent import DataAnalystAgent, LLMClient, LLMConfig
from da_agent.config import load_dotenv
from da_connectors.sqlite import SQLiteConnector
from da_evals.scenario_cx import seed_database, seed_semantics
from da_governance import JsonlAuditSink
from da_semantic import InMemorySemanticStore
from da_types import GuardPolicy, UserIdentity

ROOT = Path(__file__).parent.parent

QUESTIONS = [
    "2026年6月的GMV是多少？",
    "按渠道拆解2026年6月的GMV，哪个渠道最高？",
    "2026年7月的工单量相比6月变化如何？主要是哪类工单驱动的？",
]


async def main() -> None:
    load_dotenv(ROOT / ".env")
    db_path = ROOT / "examples" / "cx.db"
    seed_database(db_path)

    store = InMemorySemanticStore()
    await seed_semantics(store)

    audit_path = ROOT / "examples" / "audit.jsonl"
    agent = DataAnalystAgent(
        connector=SQLiteConnector("cx-sqlite", db_path),
        semantic_store=store,
        audit_sink=JsonlAuditSink(audit_path),
        llm=LLMClient(LLMConfig.from_env()),
        guard=GuardPolicy(max_result_rows=200),
    )
    analyst = UserIdentity(
        tenant_id="acme", user_id="analyst_1", claims={"allowed_databases": "main"}
    )

    for q in QUESTIONS:
        print(f"\n{'=' * 70}\n❓ {q}\n{'-' * 70}")
        answer = await agent.ask(q, analyst)
        print(answer.text)
        print(f"\n[执行了 {len(answer.executed)} 条 SQL，{answer.steps} 步]")
        for e in answer.executed:
            flag = "✓" if e.ok else "✗"
            print(f"  {flag} {e.statement[:100]}")
    print(f"\n审计链已写入 {audit_path}")


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
