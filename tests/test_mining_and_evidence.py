"""查询日志挖掘 + 证据图实体归一（4.2/4.3 旗舰测试）。

目标：从混乱命名（cust_no/client_code/customer_id）中自动归一出"客户"实体。
"""

from datetime import UTC, datetime

from da_connectors.sqlite import SQLiteConnector
from da_evals.scenario_cx import seed_database
from da_semantic import EvidenceGraph, mine_query_log, profile_catalog, value_overlaps
from da_semantic.evidence import AUTO_MERGE_THRESHOLD
from da_types import GuardPolicy, HistoricalQuery, MetadataScope, UserIdentity

IDENTITY = UserIdentity(tenant_id="t", user_id="u", claims={"allowed_databases": "main"})


def _hq(sql: str) -> HistoricalQuery:
    return HistoricalQuery(query_id="q", sql=sql, started_at=datetime.now(UTC))


SYNTH_HISTORY = [
    _hq(
        "SELECT c.region, SUM(o.order_amt) FROM orders o "
        "JOIN crm_contacts c ON o.cust_no = c.client_code "
        "WHERE o.stat = 1 AND o.cust_no NOT LIKE 'TEST%' GROUP BY c.region"
    ),
    _hq(
        "SELECT COUNT(*) FROM cs_tickets t JOIN crm_contacts c "
        "ON t.customer_id = c.client_code GROUP BY c.region"
    ),
    _hq(
        "SELECT o.chan, SUM(o.order_amt) FROM orders o "
        "JOIN crm_contacts c ON o.cust_no = c.client_code "
        "WHERE o.stat = 1 GROUP BY o.chan"
    ),
    _hq(
        "SELECT t.cat, COUNT(*) FROM cs_tickets t "
        "JOIN crm_contacts c ON t.customer_id = c.client_code "
        "WHERE c.region = '华东' GROUP BY t.cat"
    ),
    _hq("SELECT COUNT(*) FROM cs_tickets WHERE cat = '退款咨询'"),
]


def test_mining_extracts_joins_filters_aggs():
    report = mine_query_log(SYNTH_HISTORY, "sqlite")
    assert report.parsed_queries == 5

    join_pairs = {(j.left, j.right) for j in report.joins}
    assert (("crm_contacts", "client_code"), ("orders", "cust_no")) in join_pairs

    filters = dict(report.frequent_filters)
    assert any("stat = 1" in f for f in filters)          # 口径惯例被挖出
    assert any("NOT LIKE 'TEST%'" in f for f in filters)  # 隐性口径：排除测试账号

    aggs = dict(report.frequent_aggregations)
    assert any("SUM" in a for a in aggs)

    heat = dict(report.table_heat)
    assert heat["orders"] >= 2


async def test_evidence_graph_unifies_customer_entity(tmp_path):
    """三张表三个名字的客户 ID，靠 join 证据 + 值域重叠自动归一为一个簇。"""
    db = tmp_path / "cx.db"
    seed_database(db)
    connector = SQLiteConnector("cx", db)

    catalog = await connector.get_metadata(MetadataScope())
    profiles = await profile_catalog(
        connector, catalog, IDENTITY, GuardPolicy(max_result_rows=500)
    )
    overlaps = value_overlaps(profiles)
    mining = mine_query_log(SYNTH_HISTORY, "sqlite")

    graph = EvidenceGraph()
    graph.add_join_evidence(mining.joins)
    graph.add_overlap_evidence(overlaps)
    graph.add_name_similarity(profiles)
    result = graph.unify()

    customer_cluster = None
    for cluster in result.auto_clusters:
        members = set(cluster.members)
        if ("orders", "cust_no") in members and ("crm_contacts", "client_code") in members:
            customer_cluster = cluster
    assert customer_cluster is not None, f"clusters={result.auto_clusters}"
    assert ("cs_tickets", "customer_id") in set(customer_cluster.members)
    assert customer_cluster.confidence >= AUTO_MERGE_THRESHOLD


def test_name_similarity_alone_never_auto_merges():
    """列名相似只做候选召回，单独永远到不了自动合并线（4.3 铁律）。"""
    from da_semantic.evidence import WEIGHT_NAME_SIMILARITY, EvidenceEdge

    graph = EvidenceGraph()

    graph.add_edge(
        EvidenceEdge(
            left=("a", "customer_id"), right=("b", "customer_id"),
            kind="name_similarity", score=WEIGHT_NAME_SIMILARITY, detail="同名",
        )
    )
    assert graph.pair_confidence(("a", "customer_id"), ("b", "customer_id")) < 0.5


def test_veto_blocks_merge():
    """反例判决性否定：人工说过"不是同一实体"，之后任何证据都不再合并。"""
    from da_semantic.evidence import EvidenceEdge

    graph = EvidenceGraph()
    graph.add_edge(
        EvidenceEdge(left=("a", "x"), right=("b", "y"), kind="query_log",
                     score=0.95, detail="join过")
    )
    graph.add_veto(("a", "x"), ("b", "y"))
    result = graph.unify()
    assert not result.auto_clusters
    assert not result.to_confirm
