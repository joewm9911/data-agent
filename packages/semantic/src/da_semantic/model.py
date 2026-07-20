"""语义层数据模型（架构文档 4.1 的代码化）。

原子单位是业务概念（Entity/Metric），不是表/列。
归一粒度是 (table, column) 二元组——防同名不同义。
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

EvidenceKind = Literal["query_log", "value_overlap", "lineage", "name_similarity", "human"]


class Binding(BaseModel):
    """概念 → 物理落点。grain 标注防粒度冲突（明细表 vs 聚合表）。"""

    table: str  # 完整限定名 database.table
    column: str
    grain: str = ""


class JoinPath(BaseModel):
    expr: str  # 如 "orders.cust_no = crm_contacts.client_code"
    evidence: EvidenceKind
    confidence: float = Field(ge=0.0, le=1.0)


class EnumMapping(BaseModel):
    """值级归一：列名对了、过滤条件也要对。"""

    concept: str  # 如 "支付状态"
    # {"db.orders.status": {"1": "paid", "2": "pending"}}
    mappings: dict[str, dict[str, str]] = Field(default_factory=dict)


class SemanticRole(BaseModel):
    """业务角色而非数据类型：下单日期 ≠ 支付日期 ≠ 发货日期。"""

    table: str
    column: str
    role: str


class Entity(BaseModel):
    name: str
    canonical_key: str
    aliases: list[str] = Field(default_factory=list)  # 含业务黑话
    bindings: list[Binding] = Field(default_factory=list)
    join_paths: list[JoinPath] = Field(default_factory=list)
    enum_mappings: list[EnumMapping] = Field(default_factory=list)
    semantic_roles: list[SemanticRole] = Field(default_factory=list)
    # schema 漂移冻结的绑定（"table.column"），冻结绑定不进 agent 上下文（drift.py）
    frozen_bindings: list[str] = Field(default_factory=list)


class Metric(BaseModel):
    name: str
    definition: str  # 业务口径描述，回答时作为口径声明输出
    expr: str  # SQL 表达式
    grain: list[str] = Field(default_factory=list)
    verified: bool = False  # 人工确认标记
    restricted: bool = False  # 权限分区：受限层不进低权限用户上下文（6.2-3）


class CounterExample(BaseModel):
    """反例与正例同权重：防同一错误复发（4.5）。"""

    kind: Literal["bad_join", "bad_expr", "bad_binding"]
    expr: str
    reason: str


class VerifiedAnswer(BaseModel):
    """问题 → SQL → 答案三元组。复用 SQL 模板，以提问者身份重执行（6.1）。"""

    question: str
    sql_template: str
    verified_by: str
    verified_at: datetime
    restricted: bool = False
