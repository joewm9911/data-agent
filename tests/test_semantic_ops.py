"""运营端语义层管理：元数据浏览 / 勾选表快速集成归一 / 指标与实体 CRUD / 版本历史。"""

from da_connectors.dataset import DatasetStore
from fastapi.testclient import TestClient
from test_console_ops import H, make_console_client

DB_HEADERS = H


def _client(tmp_path) -> tuple[TestClient, object, object]:
    return make_console_client(tmp_path)


def test_metadata_browser(tmp_path):
    c, _, db = _client(tmp_path)
    c.post("/admin/sources", headers=H, json={
        "source_id": "cx", "kind": "sqlite", "config": {"path": str(db)}})
    meta = c.get("/admin/sources/cx/metadata", headers=H).json()
    tables = {t["name"]: t for t in meta["tables"]}
    assert set(tables) == {"orders", "crm_contacts", "cs_tickets"}
    assert tables["orders"]["row_count"] == 3000
    col_names = [col["name"] for col in tables["orders"]["columns"]]
    assert "cust_no" in col_names and "pay_dt" in col_names


def test_integrate_selected_tables_normalizes_entity(tmp_path):
    """勾选两张表集成：值域重叠证据归一出客户实体（cust_no ↔ client_code）。"""
    c, state, db = _client(tmp_path)
    c.post("/admin/sources", headers=H, json={
        "source_id": "cx", "kind": "sqlite", "config": {"path": str(db)}})

    r = c.post("/admin/sources/cx/integrate", headers=H,
               json={"tables": ["orders", "crm_contacts"]})
    body = r.json()
    assert r.status_code == 200
    # 集成范围受勾选限制：只 profiling 了两张表的列
    assert body["profiled_columns"] == 6 + 4  # orders 6 列 + crm_contacts 4 列
    # 仅值域重叠（单证据 0.83）不够自动合并线 → 正确地进确认队列（铁律 P2）
    assert body["confirmations_queued"] >= 1

    items = c.get("/admin/confirmations", headers=H).json()
    target = next(i for i in items
                  if "cust_no" in i["question"] and "client_code" in i["question"])
    # 运营者判决"是" → 确认即归一：实体直接写入语义层
    c.post(f"/admin/confirmations/{target['item_id']}/answer",
           headers=H, json={"choice": "是，同一实体"})

    entities = c.get("/admin/semantic/objects?kind=entity", headers=H).json()
    bound = {(b["table"], b["column"])
             for e in entities for b in e["payload"]["bindings"]}
    assert ("orders", "cust_no") in bound and ("crm_contacts", "client_code") in bound
    joins = [j["expr"] for e in entities for j in e["payload"]["join_paths"]]
    assert any("cust_no" in j and "client_code" in j for j in joins)

    # 空选择被拒绝
    assert c.post("/admin/sources/cx/integrate", headers=H,
                  json={"tables": []}).status_code == 400


def test_metric_crud_versioned(tmp_path):
    c, _, _ = _client(tmp_path)
    r = c.put("/admin/semantic/metrics/GMV", headers=H, json={
        "definition": "已支付订单金额", "expr": "SUM(amt) WHERE stat=1",
        "grain": ["day"], "verified": False})
    assert r.json() == {"name": "GMV", "version": 1}

    r = c.put("/admin/semantic/metrics/GMV", headers=H, json={
        "definition": "已支付订单金额，排除测试账号",
        "expr": "SUM(amt) WHERE stat=1 AND cust NOT LIKE 'TEST%'",
        "grain": ["day", "chan"], "verified": True})
    assert r.json()["version"] == 2

    objs = c.get("/admin/semantic/objects?kind=metric", headers=H).json()
    gmv = next(o for o in objs if o["name"] == "GMV")
    assert gmv["version"] == 2 and gmv["payload"]["verified"] is True
    assert gmv["updated_by"] == "admin"  # 修改人 = 操作员身份

    history = c.get("/admin/semantic/history?kind=metric&name=GMV",
                    headers=H).json()
    assert [h["version"] for h in history] == [1, 2]
    assert history[0]["payload"]["verified"] is False  # 历史完整保留

    # 不合法定义被拒
    bad = c.put("/admin/semantic/metrics/X", headers=H, json={"grain": "不是列表"})
    assert bad.status_code == 400


def test_entity_crud(tmp_path):
    c, _, _ = _client(tmp_path)
    r = c.put("/admin/semantic/entities/客户", headers=H, json={
        "canonical_key": "customer_id", "aliases": ["会员"],
        "bindings": [{"table": "orders", "column": "cust_no", "grain": ""}]})
    assert r.json()["version"] == 1
    objs = c.get("/admin/semantic/objects?kind=entity", headers=H).json()
    assert objs[0]["payload"]["aliases"] == ["会员"]


def test_integrate_preserves_pending_confirmations(tmp_path):
    """集成是增量动作：不清空既有确认队列待办。"""
    c, state, db = _client(tmp_path)
    from da_semantic.evidence import EvidenceEdge

    state.confirmations.add_entity_merge(
        EvidenceEdge(left=("a", "x"), right=("b", "y"),
                     kind="value_overlap", score=0.7, detail="旧待办"))
    c.post("/admin/sources", headers=H, json={
        "source_id": "cx", "kind": "sqlite", "config": {"path": str(db)}})
    c.post("/admin/sources/cx/integrate", headers=H,
           json={"tables": ["cs_tickets"]})
    questions = [i["question"] for i in
                 c.get("/admin/confirmations", headers=H).json()]
    assert any("旧待办" in q for q in questions)


def test_console_page_has_management_ui(tmp_path):
    c, _, _ = _client(tmp_path)
    html = c.get("/console").text
    for marker in ("映射矩阵", "集成所选表到语义层", "SQL 转换", "统计时间字段",
                   "filter 表达式", "试算", "版本历史"):
        assert marker in html
    # JS 内联 onclick 的引号转义必须存活（曾因双层转义丢失导致整页脚本失效）
    assert "showHistory(\\'entity\\'" in html
    assert "showHistory('entity'" not in html


def test_dataset_headers_unused_import_guard(tmp_path):
    assert DatasetStore  # 保留 import 供 fixture 复用方按需扩展
