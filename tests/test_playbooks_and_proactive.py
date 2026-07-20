"""Playbook 引擎（5.3）+ 主动层（5.5）。"""

import pytest
from da_agent import (
    MetricNode,
    MonitorSpec,
    PlaybookEngine,
    PlaybookRegistry,
    ProactiveMonitor,
    channel_review_playbook,
    cx_ticket_anomaly_playbook,
)
from da_connectors.sqlite import SQLiteConnector
from da_evals.scenario_cx import seed_database
from da_types import GuardPolicy, UserIdentity

IDENTITY = UserIdentity(tenant_id="t", user_id="u", claims={"allowed_databases": "main"})


@pytest.fixture(scope="module")
def db(tmp_path_factory):
    path = tmp_path_factory.mktemp("pb") / "cx.db"
    seed_database(path)
    return path


def test_registry_matches_by_keywords():
    registry = PlaybookRegistry()
    registry.register(cx_ticket_anomaly_playbook())
    registry.register(channel_review_playbook())
    assert registry.match("工单为什么激增了").name == "工单量异常诊断"
    assert registry.match("帮我做渠道复盘").name == "渠道复盘"
    assert registry.match("天气怎么样") is None


async def test_ticket_anomaly_playbook_runs(db):
    engine = PlaybookEngine(SQLiteConnector("cx", db), GuardPolicy(max_result_rows=500))
    run = await engine.run(
        cx_ticket_anomaly_playbook(),
        {"base_start": "2026-06-01", "base_end": "2026-06-30",
         "curr_start": "2026-07-01", "curr_end": "2026-07-31"},
        IDENTITY,
    )
    assert len(run.results) == 3
    by_cat = {r.title: r for r in run.results}["按类型分解（当期）"]
    assert by_cat.rows[0][0] == "退款咨询"  # 尖峰类型排第一
    assert "工单量异常诊断" in run.narrative()


async def test_playbook_missing_param_raises(db):
    engine = PlaybookEngine(SQLiteConnector("cx", db))
    with pytest.raises(ValueError, match="缺少参数"):
        await engine.run(cx_ticket_anomaly_playbook(), {}, IDENTITY)


async def test_proactive_detects_spike_and_attributes(db):
    """监控 → z-score 异常 → 自动归因 → 带诊断结论的简报（不是裸告警）。"""
    monitor = ProactiveMonitor(SQLiteConnector("cx", db), GuardPolicy(max_result_rows=1000))
    spec = MonitorSpec(
        name="工单量日监控",
        metric=MetricNode(
            name="工单量",
            value_sql="SELECT COUNT(*) FROM cs_tickets WHERE {where}",
            dimensions={
                "工单类型": "SELECT cat, COUNT(*) FROM cs_tickets WHERE {where} GROUP BY cat"
            },
        ),
        daily_sql=(
            "SELECT created_at, COUNT(*) FROM cs_tickets WHERE {where} "
            "GROUP BY created_at ORDER BY created_at"
        ),
        z_threshold=2.0,
        base_where_tpl="created_at BETWEEN '2026-06-01' AND '2026-06-30'",
        current_where_tpl="created_at BETWEEN '2026-07-01' AND '2026-07-31'",
    )
    briefing = await monitor.run(spec, IDENTITY)
    assert briefing.anomalies, "7 月尖峰应被检出"
    assert all(a.day.startswith("2026-07") for a in briefing.anomalies)
    assert briefing.attribution is not None
    assert "退款咨询" in briefing.text  # 简报自带诊断结论
