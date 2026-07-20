"""C 类特性：归因深化 / LLM 增强（fake llm）/ 模糊召回 / 漂移 / 差分审计 / eval 生成。"""

from datetime import UTC, datetime, timedelta

import pytest
from da_agent.metric_tree import MetricNode, MetricTreeEngine, draft_metric_trees
from da_connectors.sqlite import SQLiteConnector
from da_evals.generate import generate_eval_cases, trend_markdown
from da_evals.harness import EvalReport
from da_evals.scenario_cx import seed_database
from da_governance.differential import DifferentialAuditDetector
from da_platform.vector import NgramIndex
from da_semantic import (
    ConfirmationQueue,
    EvidenceGraph,
    InMemorySemanticStore,
    LearningLoop,
    profile_catalog,
)
from da_semantic.drift import apply_drift_freeze, diff_catalogs
from da_semantic.enrichment import SemanticEnricher
from da_types import (
    CatalogSnapshot,
    ColumnMeta,
    GuardPolicy,
    HistoricalQuery,
    MetadataScope,
    TableMeta,
    UserIdentity,
)

IDENTITY = UserIdentity(tenant_id="t", user_id="u", claims={"allowed_databases": "main"})


@pytest.fixture(scope="module")
def db(tmp_path_factory):
    path = tmp_path_factory.mktemp("c") / "cx.db"
    seed_database(path)
    return path


# ---- 归因深化：乘法因子 + 递归下钻 ----


async def test_attribution_factors_and_drilldown(db):
    tree = MetricNode(
        name="GMV",
        value_sql=(
            "SELECT COALESCE(SUM(order_amt),0) FROM orders "
            "WHERE stat=1 AND cust_no NOT LIKE 'TEST%' AND {where}"
        ),
        dimensions={
            "渠道": (
                "SELECT chan, SUM(order_amt) FROM orders "
                "WHERE stat=1 AND cust_no NOT LIKE 'TEST%' AND {where} GROUP BY chan"
            ),
            "订单状态": (
                "SELECT stat, SUM(order_amt) FROM orders WHERE {where} GROUP BY stat"
            ),
        },
        drill_filters={"渠道": "chan = '{member}'"},
        factors=[
            MetricNode(
                name="订单量",
                value_sql=(
                    "SELECT COUNT(*) FROM orders "
                    "WHERE stat=1 AND cust_no NOT LIKE 'TEST%' AND {where}"
                ),
            ),
            MetricNode(
                name="客单价",
                value_sql=(
                    "SELECT COALESCE(AVG(order_amt),0) FROM orders "
                    "WHERE stat=1 AND cust_no NOT LIKE 'TEST%' AND {where}"
                ),
            ),
        ],
    )
    engine = MetricTreeEngine(SQLiteConnector("cx", db), GuardPolicy(max_result_rows=1000))
    report = await engine.attribute(
        tree,
        base_where="pay_dt BETWEEN '2026-05-01' AND '2026-05-31'",
        current_where="pay_dt BETWEEN '2026-06-01' AND '2026-06-30'",
        identity=IDENTITY,
        drill_depth=1,
    )
    # 乘法分解：订单量×客单价 的贡献之和 ≈ 总 delta（连乘替代法恒等式）
    assert len(report.factor_steps) == 2
    assert sum(f.contribution for f in report.factor_steps) == pytest.approx(
        report.delta, rel=1e-6
    )
    # 递归下钻：top 渠道内部继续分解了次级维度
    assert report.drill_down is not None
    assert report.drill_member.startswith("渠道=")
    assert report.drill_down.steps  # 子报告有次级维度分解
    assert "↳ 下钻" in report.narrative()


async def test_draft_metric_trees_from_profiles(db):
    connector = SQLiteConnector("cx", db)
    catalog = await connector.get_metadata(MetadataScope())
    profiles = await profile_catalog(connector, catalog, IDENTITY)
    trees = draft_metric_trees(catalog, profiles)
    assert "cs_tickets量" in trees
    tree = trees["cs_tickets量"]
    assert "cat" in tree.dimensions and "cat" in tree.drill_filters
    # 草稿树可直接跑归因
    engine = MetricTreeEngine(connector, GuardPolicy(max_result_rows=1000))
    report = await engine.attribute(
        tree,
        base_where="created_at BETWEEN '2026-06-01' AND '2026-06-30'",
        current_where="created_at BETWEEN '2026-07-01' AND '2026-07-31'",
        identity=IDENTITY, drill_depth=0,
    )
    assert report.delta > 0


# ---- LLM 语义增强（fake llm，确定性）----


class FakeComplete:
    def __init__(self, response: str):
        self.response = response
        self.prompts: list[str] = []

    async def __call__(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.response


async def test_enricher_column_descriptions():
    fake = FakeComplete('```json\n{"orders.cust_no": "客户编号", "orders.stat": "?"}\n```')
    enricher = SemanticEnricher(fake)
    catalog = CatalogSnapshot(
        source_id="x", captured_at=datetime.now(UTC),
        tables=[TableMeta(database="main", name="orders",
                          columns=[ColumnMeta(name="cust_no", type="TEXT"),
                                   ColumnMeta(name="stat", type="INT")])],
    )
    out = await enricher.describe_columns(catalog, [])
    assert out == {("orders", "cust_no"): "客户编号"}  # "?" 被过滤（不确定不猜）


async def test_enricher_enum_questions_into_queue():
    fake = FakeComplete('{"orders.stat": {"1": "已支付", "2": "待支付"}}')
    enricher = SemanticEnricher(fake)
    store = InMemorySemanticStore()
    queue = ConfirmationQueue(store, EvidenceGraph())

    from da_semantic.profiling import ColumnProfile

    profiles = [ColumnProfile(table="orders", column="stat", sampled=100,
                              distinct=2, is_enum=True, enum_values=["1", "2"])]
    items = await enricher.draft_enum_questions(profiles, queue)
    assert len(items) == 1
    assert "已支付" in items[0].question
    assert queue.pending()  # LLM 只出题，人判决


async def test_enricher_doc_mining():
    fake = FakeComplete(
        '{"GMV": {"definition": "已支付订单金额，排除测试账号", "expr_hint": "SUM(order_amt)"}}'
    )
    out = await SemanticEnricher(fake).mine_documents(["《指标口径手册》GMV 定义为……"])
    assert out["GMV"]["definition"].startswith("已支付")


async def test_enricher_tolerates_bad_llm_output():
    out = await SemanticEnricher(FakeComplete("我不知道")).mine_documents(["x"])
    assert out == {}  # LLM 输出不可解析 → 空结果，不炸不编


# ---- 模糊召回 ----


async def test_verified_answer_fuzzy_recall():
    store = InMemorySemanticStore()
    loop = LearningLoop(store, index=NgramIndex())
    await loop.record_verified_answer("2026年6月的GMV是多少", "SELECT ...", actor="cfo")

    hit = await loop.find_verified_answer("6月GMV是多少？")  # 不同问法
    assert hit is not None and hit.verified_by == "cfo"
    assert await loop.find_verified_answer("客服工单趋势如何") is None  # 不相关不命中


# ---- schema 漂移 ----


async def test_drift_detect_and_freeze():
    old = CatalogSnapshot(
        source_id="x", captured_at=datetime.now(UTC),
        tables=[TableMeta(database="main", name="orders",
                          columns=[ColumnMeta(name="cust_no", type="TEXT"),
                                   ColumnMeta(name="amt", type="REAL")])],
    )
    new = CatalogSnapshot(
        source_id="x", captured_at=datetime.now(UTC),
        tables=[TableMeta(database="main", name="orders",
                          columns=[ColumnMeta(name="cust_no", type="INT"),  # 类型突变
                                   ColumnMeta(name="chan", type="TEXT")])],  # amt 删除
    )
    alerts = diff_catalogs(old, new)
    kinds = {(a.kind, a.column) for a in alerts}
    assert ("type_changed", "cust_no") in kinds
    assert ("column_removed", "amt") in kinds
    assert ("column_added", "chan") in kinds

    store = InMemorySemanticStore()
    await store.put("entity", "客户", {
        "name": "客户", "canonical_key": "cid", "aliases": [],
        "bindings": [{"table": "orders", "column": "cust_no", "grain": ""}],
        "join_paths": [], "enum_mappings": [], "semantic_roles": [],
    }, "boot")
    report = await apply_drift_freeze(store, alerts)
    assert report.frozen_bindings == ["客户: orders.cust_no"]
    record = await store.get("entity", "客户")
    assert record.version == 2 and "orders.cust_no" in record.payload["frozen_bindings"]


# ---- 差分审计 ----


def test_differential_detector_alerts_on_probing():
    clock = [0.0]
    det = DifferentialAuditDetector(query_threshold=3, small_result_rows=5,
                                    clock=lambda: clock[0])
    sqls = [
        "SELECT dept, AVG(salary) FROM emp GROUP BY dept",
        "SELECT AVG(salary) FROM emp WHERE dept='研发'",
        "SELECT AVG(salary) FROM emp WHERE dept='研发' AND name != '张三'",
    ]
    alerts = []
    for i, sql in enumerate(sqls):
        clock[0] += 10
        alerts = det.observe("t1", "u1", sql, "sqlite", result_rows=1 if i else 8)
    assert alerts and alerts[0].table == "emp" and alerts[0].small_results == 2

    # 非聚合查询不计入
    assert det.observe("t1", "u1", "SELECT * FROM emp", "sqlite", 100) == []


# ---- eval 自动生成 + 趋势 ----


async def test_generate_eval_cases_from_history(db):
    connector = SQLiteConnector("cx", db)
    history = [
        HistoricalQuery(query_id="h1", started_at=datetime.now(UTC),
                        sql="SELECT COUNT(*) FROM cs_tickets WHERE cat='退款咨询'"),
        HistoricalQuery(query_id="h2", started_at=datetime.now(UTC),
                        sql="DROP TABLE orders"),  # 非 SELECT 被跳过
    ]
    cases = await generate_eval_cases(history, connector, IDENTITY)
    assert len(cases) == 1
    assert cases[0].case.expected  # golden 数值来自独立执行
    assert cases[0].case.any_of


def test_trend_markdown_regression_verdict():
    r1 = EvalReport(ran_at=datetime.now(UTC) - timedelta(days=7))
    r2 = EvalReport(ran_at=datetime.now(UTC))
    from da_evals import EvalCase
    from da_evals.harness import CaseResult

    case = EvalCase(case_id="c", question="q", expected=["1"])
    r1.results = [CaseResult(case=case, answer_text="1", passed=True)]
    r2.results = [CaseResult(case=case, answer_text="0", passed=False)]
    md = trend_markdown([r1, r2])
    assert "准确率回退" in md
    md_ok = trend_markdown([r2, r1]) if False else trend_markdown([r1])
    assert "飞轮" in md_ok
