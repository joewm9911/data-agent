"""指标八要素模型：组件/口径文本/时间口径一致性校验/试算/表达式片段校验。"""

import pytest
from da_connectors.sqlite import SQLiteConnector
from da_evals.scenario_cx import golden, seed_database
from da_governance import validate_expression_fragment
from da_semantic import (
    Binding,
    Entity,
    Metric,
    MetricComponent,
    SemanticRole,
    trial_metric,
    validate_metric,
)
from da_types import UserIdentity

IDENTITY = UserIdentity(tenant_id="t", user_id="u", claims={"allowed_databases": "main"})

ENTITIES = [Entity(
    name="客户", canonical_key="customer_id",
    bindings=[Binding(table="orders", column="cust_no")],
    semantic_roles=[
        SemanticRole(table="orders", column="pay_dt", role="支付日期"),
        SemanticRole(table="cs_tickets", column="created_at", role="工单创建日期"),
    ],
)]


def make_ratio(time_field="工单创建日期", den_table="cs_tickets") -> Metric:
    return Metric(
        name="工单解决率", definition="已解决工单占比",
        numerator=MetricComponent(expr="SUM(resolved)", description="已解决工单数",
                                  table="cs_tickets"),
        denominator=MetricComponent(expr="COUNT(*)", description="全部工单数",
                                    table=den_table),
        time_field=time_field,
    )


def test_caliber_text_includes_components():
    text = make_ratio().caliber_text()
    assert "分子：SUM(resolved)" in text and "分母：COUNT(*)" in text
    assert "统计时间字段：工单创建日期" in text and "口径一致" in text


def test_validate_time_consistency():
    assert validate_metric(make_ratio(), ENTITIES) == []

    # 跨表：分母表 orders 有"支付日期"但没有"工单创建日期" → 明确报错
    errors = validate_metric(make_ratio(den_table="orders"), ENTITIES)
    assert errors == ["请先在映射矩阵为表 orders 绑定语义角色 工单创建日期"]

    # 缺分子表
    m = make_ratio()
    m.numerator.table = ""
    assert "分子必须指定数据表" in validate_metric(m, ENTITIES)

    # 旧式 expr 兼容：无 numerator 有 expr → 通过
    legacy = Metric(name="X", definition="d", expr="SUM(a)")
    assert validate_metric(legacy, ENTITIES) == []


async def test_trial_matches_golden(tmp_path):
    """试算 = 真实执行：与独立 golden SQL 一致。"""
    db = tmp_path / "cx.db"
    seed_database(db)
    connector = SQLiteConnector("cx", db)

    trial = await trial_metric(
        connector, make_ratio(), ENTITIES, IDENTITY, "2026-07-01", "2026-07-31"
    )
    g_num = golden(db, "SELECT SUM(resolved) FROM cs_tickets "
                       "WHERE created_at BETWEEN '2026-07-01' AND '2026-07-31'")[0][0]
    g_den = golden(db, "SELECT COUNT(*) FROM cs_tickets "
                       "WHERE created_at BETWEEN '2026-07-01' AND '2026-07-31'")[0][0]
    assert trial.numerator_value == g_num
    assert trial.denominator_value == g_den
    assert trial.ratio == pytest.approx(g_num / g_den)
    assert "BETWEEN '2026-07-01'" in trial.numerator_sql

    # 校验失败直接拒算
    with pytest.raises(ValueError, match="绑定语义角色"):
        await trial_metric(connector, make_ratio(den_table="orders"),
                           ENTITIES, IDENTITY, "2026-07-01", "2026-07-31")


async def test_gmv_new_model_trial(tmp_path):
    """场景 GMV（新模型：分子+filter+时间字段）试算 = golden。"""
    db = tmp_path / "cx.db"
    seed_database(db)
    gmv = Metric(
        name="GMV", definition="d",
        numerator=MetricComponent(expr="ROUND(SUM(order_amt), 2)", table="orders",
                                  filter="stat = 1 AND cust_no NOT LIKE 'TEST%'"),
        time_field="支付日期",
    )
    trial = await trial_metric(
        SQLiteConnector("cx", db), gmv, ENTITIES, IDENTITY,
        "2026-06-01", "2026-06-30",
    )
    from da_evals.scenario_cx import GOLDEN_GMV_JUNE

    assert trial.numerator_value == golden(db, GOLDEN_GMV_JUNE)[0][0]


def test_expression_fragment_guard():
    assert validate_expression_fragment("strftime('%Y-%m', pay_dt)", "sqlite") is None
    assert validate_expression_fragment("SUM(order_amt)", "sqlite") is None
    assert "子查询" in validate_expression_fragment(
        "(SELECT MAX(x) FROM other)", "sqlite")
    assert "分号" in validate_expression_fragment("1; DROP TABLE x", "sqlite")
    assert validate_expression_fragment("((", "sqlite") is not None
