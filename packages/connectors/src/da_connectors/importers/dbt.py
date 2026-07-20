"""BI/建模资产导入（架构文档 3.2 模式三 / 4.2 信号二）：dbt manifest 解析器。

白捡客户已付费的建模成果：models 的表/列描述 + metrics 定义 → 语义层草稿素材。
输入为 dbt manifest.json 的 dict（调用方负责读文件），输出为语义层可直接入库的草稿。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class DbtImport:
    # 表描述：table -> description
    table_docs: dict[str, str] = field(default_factory=dict)
    # 列描述：(table, column) -> description
    column_docs: dict[tuple[str, str], str] = field(default_factory=dict)
    # 指标草稿：name -> {definition, expr, grain}
    metric_drafts: dict[str, dict[str, Any]] = field(default_factory=dict)


def import_dbt_manifest(manifest: dict[str, Any]) -> DbtImport:
    result = DbtImport()

    for node in manifest.get("nodes", {}).values():
        if node.get("resource_type") != "model":
            continue
        table = node.get("alias") or node.get("name", "")
        if not table:
            continue
        if node.get("description"):
            result.table_docs[table] = node["description"]
        for col_name, col in (node.get("columns") or {}).items():
            if col.get("description"):
                result.column_docs[(table, col_name)] = col["description"]

    for metric in manifest.get("metrics", {}).values():
        name = metric.get("label") or metric.get("name", "")
        if not name:
            continue
        type_params = metric.get("type_params") or {}
        measure = (type_params.get("measure") or {}).get("name", "")
        result.metric_drafts[name] = {
            "definition": metric.get("description", ""),
            "expr": metric.get("expr") or measure or "",
            "grain": [
                d if isinstance(d, str) else d.get("name", "")
                for d in metric.get("dimensions", [])
            ],
        }

    return result
