"""端到端演示：真实 CX 场景 + MiniMax（Anthropic 兼容端点）。

用法：uv run python examples/demo_cx.py
"""

import asyncio
import sys
from pathlib import Path

from da_agent import (
    DataAnalystAgent,
    LLMClient,
    LLMConfig,
    MetricNode,
    MonitorSpec,
    ProactiveMonitor,
    render_briefing_report,
)
from da_agent.config import load_dotenv
from da_connectors.sqlite import SQLiteConnector
from da_evals.scenario_cx import seed_database, seed_semantics
from da_governance import JsonlAuditSink
from da_semantic import InMemorySemanticStore
from da_types import GuardPolicy, UserIdentity

TICKET_TREE = MetricNode(
    name="工单量",
    value_sql="SELECT COUNT(*) FROM cs_tickets WHERE {where}",
    dimensions={
        "工单类型": "SELECT cat, COUNT(*) FROM cs_tickets WHERE {where} GROUP BY cat",
    },
)

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
    connector = SQLiteConnector("cx-sqlite", db_path)
    agent = DataAnalystAgent(
        connector=connector,
        semantic_store=store,
        audit_sink=JsonlAuditSink(audit_path),
        llm=LLMClient(LLMConfig.from_env()),
        guard=GuardPolicy(max_result_rows=200),
        metric_trees={"工单量": TICKET_TREE},
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

    # 主动层演示：监控 → 异常检测 → 自动归因 → 带诊断的晨报（无 LLM，确定性）
    print(f"\n{'=' * 70}\n📡 主动层：工单量日监控\n{'-' * 70}")
    monitor = ProactiveMonitor(connector, GuardPolicy(max_result_rows=1000))
    briefing = await monitor.run(
        MonitorSpec(
            name="工单量日监控",
            metric=TICKET_TREE,
            daily_sql=(
                "SELECT created_at, COUNT(*) FROM cs_tickets WHERE {where} "
                "GROUP BY created_at ORDER BY created_at"
            ),
            z_threshold=2.0,
            base_where_tpl="created_at BETWEEN '2026-06-01' AND '2026-06-30'",
            current_where_tpl="created_at BETWEEN '2026-07-01' AND '2026-07-31'",
        ),
        analyst,
    )
    report = render_briefing_report(briefing)
    print(report.markdown)
    print(f"\n审计链已写入 {audit_path}")


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
