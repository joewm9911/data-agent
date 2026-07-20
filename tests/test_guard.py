"""护栏语义测试：失败必须表现为拒绝，改写后语句是唯一可执行版本。"""

from da_governance import prepare_statement
from da_types import GuardPolicy

POLICY = GuardPolicy(max_result_rows=1000)


def test_select_allowed_and_limit_injected():
    d = prepare_statement("SELECT id, name FROM orders", "clickhouse", POLICY)
    assert d.allowed
    assert "LIMIT 1000" in d.rewritten_statement


def test_existing_small_limit_kept():
    d = prepare_statement("SELECT id FROM orders LIMIT 10", "clickhouse", POLICY)
    assert d.allowed
    assert "LIMIT 10" in d.rewritten_statement


def test_oversized_limit_clamped():
    d = prepare_statement("SELECT id FROM orders LIMIT 999999", "clickhouse", POLICY)
    assert d.allowed
    assert "LIMIT 1000" in d.rewritten_statement


def test_insert_rejected_in_read_only():
    d = prepare_statement("INSERT INTO orders VALUES (1)", "clickhouse", POLICY)
    assert not d.allowed


def test_drop_rejected():
    d = prepare_statement("DROP TABLE orders", "clickhouse", POLICY)
    assert not d.allowed


def test_multi_statement_rejected():
    d = prepare_statement("SELECT 1; SELECT 2", "clickhouse", POLICY)
    assert not d.allowed


def test_cte_select_allowed():
    d = prepare_statement(
        "WITH t AS (SELECT id FROM orders) SELECT * FROM t", "clickhouse", POLICY
    )
    assert d.allowed


def test_unparsable_rejected():
    d = prepare_statement("SELEC id FRM", "clickhouse", POLICY)
    assert not d.allowed


def test_referenced_objects_extraction():
    from da_governance import referenced_objects

    objs = referenced_objects(
        "SELECT * FROM orders o JOIN crm_contacts c ON o.cust_no = c.client_code",
        "sqlite",
    )
    assert objs == [("main", "crm_contacts"), ("main", "orders")]


def test_referenced_objects_cte_not_counted():
    from da_governance import referenced_objects

    objs = referenced_objects(
        "WITH t AS (SELECT * FROM orders) SELECT * FROM t", "sqlite"
    )
    assert objs == [("main", "orders")]
