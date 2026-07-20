"""端到端真实场景测试（需要 LLM key，CI 无 key 时跳过）。

链路：混乱命名的业务库 → 语义层注入 → agent 多步查询 → 四件套回答。
验证方式：golden 答案由纯 SQL 独立计算，断言 agent 回答包含正确数字（8.3 eval as test）。
"""

import os
import re

import pytest
from da_agent import DataAnalystAgent, LLMClient, LLMConfig, MetricNode
from da_connectors.sqlite import SQLiteConnector
from da_evals.scenario_cx import (
    GOLDEN_GMV_JUNE,
    GOLDEN_JULY_TICKET_TOP_CAT,
    GOLDEN_TOP_CHANNEL_JUNE,
    golden,
    seed_database,
    seed_semantics,
)
from da_governance import InMemoryAuditSink
from da_semantic import InMemorySemanticStore
from da_types import GuardPolicy, UserIdentity

pytestmark = pytest.mark.skipif(
    not os.environ.get("DA_LLM_API_KEY"), reason="需要 DA_LLM_API_KEY（.env）"
)

ANALYST = UserIdentity(
    tenant_id="acme",
    user_id="analyst_1",
    claims={"allowed_databases": "main"},
)


def normalize(text: str) -> str:
    """去掉千分位逗号/空格，便于数字匹配。"""
    return re.sub(r"(?<=\d)[,，\s](?=\d)", "", text)


@pytest.fixture(scope="module")
def scenario(tmp_path_factory):
    db_path = tmp_path_factory.mktemp("cx") / "cx.db"
    seed_database(db_path)
    return db_path


TICKET_TREE = MetricNode(
    name="工单量",
    value_sql="SELECT COUNT(*) FROM cs_tickets WHERE {where}",
    dimensions={
        "工单类型": "SELECT cat, COUNT(*) FROM cs_tickets WHERE {where} GROUP BY cat",
    },
)


@pytest.fixture()
async def agent(scenario):
    store = InMemorySemanticStore()
    await seed_semantics(store)
    sink = InMemoryAuditSink()
    a = DataAnalystAgent(
        connector=SQLiteConnector("cx-sqlite", scenario),
        semantic_store=store,
        audit_sink=sink,
        llm=LLMClient(LLMConfig.from_env()),
        guard=GuardPolicy(max_result_rows=200),
        metric_trees={"工单量": TICKET_TREE},
    )
    return a


async def test_gmv_with_correct_caliber(scenario, agent):
    """口径题：必须排除测试账号、只算已支付、按支付日期。"""
    expected = golden(scenario, GOLDEN_GMV_JUNE)[0][0]  # e.g. 123456.78
    answer = await agent.ask("2026年6月的GMV是多少？", ANALYST)

    text = normalize(answer.text)
    # 允许四舍五入到元的表述
    assert (str(expected) in text) or (str(round(expected)) in text) or (
        f"{expected:.2f}" in text
    ), f"golden={expected}, answer={answer.text[:500]}"
    assert any(e.ok for e in answer.executed)
    # 审计链完整：question → generation → guard → execution → presentation
    stages = [e.stage for e in agent.audit_sink.events]
    for s in ("question", "generation", "guard", "execution", "presentation"):
        assert s in stages


async def test_channel_breakdown(scenario, agent):
    """分组题 + 枚举归一：chan 编码 'dy' 应被翻译为业务语言。"""
    rows = golden(scenario, GOLDEN_TOP_CHANNEL_JUNE)
    top_chan_code = rows[0][0]
    chan_names = {"tb": "淘宝", "jd": "京东", "dy": "抖音", "web": "官网"}
    answer = await agent.ask("按渠道拆解2026年6月的GMV，哪个渠道最高？", ANALYST)
    assert chan_names[top_chan_code] in answer.text or top_chan_code in answer.text


async def test_cross_table_and_attribution(scenario, agent):
    """跨表 + 简单归因：7月工单上涨主要由哪类驱动（golden：退款咨询）。"""
    rows = golden(scenario, GOLDEN_JULY_TICKET_TOP_CAT)
    top_cat = rows[0][0]
    assert top_cat == "退款咨询"  # 场景注入的尖峰
    answer = await agent.ask(
        "2026年7月的工单量相比6月变化如何？主要是哪类工单驱动的？", ANALYST
    )
    assert "退款" in answer.text


async def test_attribution_tool_used_by_llm(scenario, agent):
    """归因引擎经 LLM 工具调用：'为什么'类问题应触发 run_attribution 并给出正确驱动。"""
    answer = await agent.ask(
        "为什么2026年7月的工单量比6月高？用归因工具分析", ANALYST
    )
    assert "退款" in answer.text
    assert any(e.statement.startswith("attribution:") and e.ok for e in answer.executed), (
        f"未调用归因工具: {[e.statement for e in answer.executed]}"
    )


async def test_no_permission_no_data(scenario):
    """权限裁剪：无权用户的上下文不含任何表，应表现为'没有数据'而非泄露表名。"""
    store = InMemorySemanticStore()
    await seed_semantics(store)
    nobody = UserIdentity(tenant_id="acme", user_id="outsider", claims={})
    a = DataAnalystAgent(
        connector=SQLiteConnector("cx-sqlite", scenario),
        semantic_store=store,
        audit_sink=InMemoryAuditSink(),
        llm=LLMClient(LLMConfig.from_env()),
    )
    answer = await a.ask("6月GMV是多少？", nobody)
    # 不应给出任何具体数字结论（允许模型解释无数据/数据不足）
    assert not any(e.ok and e.row_count > 0 for e in answer.executed)
