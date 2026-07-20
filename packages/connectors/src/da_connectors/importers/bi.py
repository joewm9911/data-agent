"""BI 数据集解析器（3.2 模式三）：Superset / Metabase / 通用映射。

每个 BI 数据集 = 别人已验证的 join + 字段重命名 + 口径封装，导入即得语义层素材。
输出统一为 BIDatasetImport（与 dbt 导入同构，供冷启动流水线消费）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class BIDatasetImport:
    # 表/数据集描述
    table_docs: dict[str, str] = field(default_factory=dict)
    # (table, column) -> 业务名/描述（字段重命名即口径线索）
    column_docs: dict[tuple[str, str], str] = field(default_factory=dict)
    # 指标草稿：name -> {definition, expr}
    metric_drafts: dict[str, dict[str, Any]] = field(default_factory=dict)
    # 数据集内的 SQL（供查询日志挖掘复用：里面藏着 join 和过滤惯例）
    embedded_sql: list[str] = field(default_factory=list)


def import_superset_dataset(dataset: dict[str, Any]) -> BIDatasetImport:
    """Superset dataset export（YAML 转 dict 后传入）。"""
    result = BIDatasetImport()
    table = dataset.get("table_name", "")
    if table:
        if dataset.get("description"):
            result.table_docs[table] = dataset["description"]
        if dataset.get("sql"):
            result.embedded_sql.append(dataset["sql"])
        for col in dataset.get("columns", []):
            label = col.get("verbose_name") or col.get("description")
            if label and col.get("column_name"):
                result.column_docs[(table, col["column_name"])] = label
        for m in dataset.get("metrics", []):
            name = m.get("verbose_name") or m.get("metric_name", "")
            if name:
                result.metric_drafts[name] = {
                    "definition": m.get("description", ""),
                    "expr": m.get("expression", ""),
                }
    return result


def import_metabase_card(card: dict[str, Any]) -> BIDatasetImport:
    """Metabase question/card export。native SQL 卡片的查询直接进挖掘信号。"""
    result = BIDatasetImport()
    native = ((card.get("dataset_query") or {}).get("native") or {}).get("query")
    if native:
        result.embedded_sql.append(native)
    name = card.get("name", "")
    if name and native:
        result.metric_drafts[name] = {
            "definition": card.get("description") or f"Metabase 卡片「{name}」",
            "expr": native,
        }
    for col in card.get("result_metadata") or []:
        table = col.get("table_name") or ""
        if table and col.get("display_name") and col.get("name"):
            result.column_docs[(table, col["name"])] = col["display_name"]
    return result


def import_generic_dataset(payload: dict[str, Any]) -> BIDatasetImport:
    """通用映射格式（帆软/QuickBI 等无公开标准的 BI，经导出脚本转成本格式）：

    {"table": str, "description": str, "sql": str,
     "columns": [{"name","label"}], "metrics": [{"name","definition","expr"}]}
    """
    result = BIDatasetImport()
    table = payload.get("table", "")
    if payload.get("description") and table:
        result.table_docs[table] = payload["description"]
    if payload.get("sql"):
        result.embedded_sql.append(payload["sql"])
    for col in payload.get("columns", []):
        if table and col.get("name") and col.get("label"):
            result.column_docs[(table, col["name"])] = col["label"]
    for m in payload.get("metrics", []):
        if m.get("name"):
            result.metric_drafts[m["name"]] = {
                "definition": m.get("definition", ""),
                "expr": m.get("expr", ""),
            }
    return result


def merge_imports(*imports: BIDatasetImport) -> BIDatasetImport:
    merged = BIDatasetImport()
    for imp in imports:
        merged.table_docs.update(imp.table_docs)
        merged.column_docs.update(imp.column_docs)
        merged.metric_drafts.update(imp.metric_drafts)
        merged.embedded_sql.extend(imp.embedded_sql)
    return merged
