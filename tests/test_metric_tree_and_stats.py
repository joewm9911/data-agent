"""指标树归因引擎（5.2，确定性无 LLM）+ 统计守门员（5.4）。"""

import pytest
from da_agent import MetricNode, MetricTreeEngine
from da_agent.stats_guard import (
    check_mom_seasonality,
    check_simpson,
    check_small_sample,
    two_proportion_significance,
)
from da_connectors.sqlite import SQLiteConnector
from da_evals.scenario_cx import seed_database
from da_types import GuardPolicy, UserIdentity

IDENTITY = UserIdentity(tenant_id="t", user_id="u", claims={"allowed_databases": "main"})

TICKET_TREE = MetricNode(
    name="工单量",
    value_sql="SELECT COUNT(*) FROM cs_tickets WHERE {where}",
    dimensions={
        "工单类型": (
            "SELECT cat, COUNT(*) FROM cs_tickets WHERE {where} GROUP BY cat"
        ),
        "解决状态": (
            "SELECT resolved, COUNT(*) FROM cs_tickets WHERE {where} GROUP BY resolved"
        ),
    },
)


@pytest.fixture(scope="module")
def db(tmp_path_factory):
    path = tmp_path_factory.mktemp("mt") / "cx.db"
    seed_database(path)
    return path


async def test_attribution_finds_injected_driver(db):
    """结构化归因（无 LLM）：7 月 vs 6 月工单，top 驱动必须是注入的退款咨询尖峰。"""
    engine = MetricTreeEngine(SQLiteConnector("cx", db), GuardPolicy(max_result_rows=1000))
    report = await engine.attribute(
        TICKET_TREE,
        base_where="created_at BETWEEN '2026-06-01' AND '2026-06-30'",
        current_where="created_at BETWEEN '2026-07-01' AND '2026-07-31'",
        identity=IDENTITY,
        base_label="2026-06",
        current_label="2026-07",
    )
    assert report.delta > 0
    top = report.steps[0].top
    assert top is not None and top.member == "退款咨询"
    assert top.share_of_total_delta > 0.5  # 贡献过半
    assert len(report.evidence_sql) >= 4   # 证据链完整
    assert "退款咨询" in report.narrative()


def test_stats_guard_significance():
    sig, p, warn = two_proportion_significance(500, 1000, 300, 1000)
    assert sig and p < 0.01 and warn is None

    sig, p, warn = two_proportion_significance(51, 100, 49, 100)
    assert not sig and warn is not None and "不显著" in warn.message


def test_stats_guard_small_sample_and_calendar():
    assert check_small_sample(10) is not None
    assert check_small_sample(100) is None
    warn = check_mom_seasonality("2026-06", "2026-07")
    assert warn is not None and "天数不同" in warn.message
    assert check_mom_seasonality("2026-07", "2026-08") is None


def test_stats_guard_simpson():
    # 整体上涨但所有分组下跌 → 悖论提示
    warn = check_simpson(overall_delta=10.0, group_deltas=[-3.0, -5.0, -1.0])
    assert warn is not None and "辛普森" in warn.message
    assert check_simpson(10.0, [3.0, 5.0]) is None
