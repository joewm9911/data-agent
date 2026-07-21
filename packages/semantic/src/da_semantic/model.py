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
    """概念 → 物理落点。grain 标注防粒度冲突（明细表 vs 聚合表）。

    expr 非空时为 SQL 转换绑定（表达式而非裸列，如 strftime('%Y-%m', pay_dt)）；
    column 仍记录主要来源列，供漂移冻结追踪。
    """

    table: str  # 完整限定名 database.table
    column: str
    grain: str = ""
    expr: str = ""  # SQL 转换片段（只允许引用本表字段，禁止子查询）


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


class MetricComponent(BaseModel):
    """分子/分母组件：底层各自独立携带表与 filter（支持跨表比率与分侧过滤）。

    配置层当前只开放指标级单表单 filter，保存时写入两个组件（模型与配置分层）。
    expr 为聚合表达式（如 COUNT(*)、SUM(order_amt)），不含 WHERE。
    """

    expr: str
    description: str = ""
    table: str = ""
    filter: str = ""  # 组件级过滤（SQL 片段，可空）


class Metric(BaseModel):
    """指标八要素：名/别名、描述、关联表、统计时间字段、filter、分子+描述、分母+描述。

    - time_field 引用语义角色名（指标级单一字段——分子分母时间口径强制一致）；
      跨表时该语义角色必须在两张表都有绑定（保存/试算强制校验）。
    - denominator 为 None 即单一聚合指标。
    - expr 为旧式单表达式字段（向后兼容；numerator 存在时优先用组件模型）。
    """

    name: str
    definition: str  # 指标描述，回答时作为口径声明输出
    expr: str = ""  # 旧式 SQL 表达式（兼容）
    numerator: MetricComponent | None = None
    denominator: MetricComponent | None = None
    time_field: str = ""  # 统计时间的语义层字段（语义角色名引用）
    # 业务黑话别名（"成交额"="GMV"）：指标直连匹配的判决依据，澄清即沉淀
    aliases: list[str] = Field(default_factory=list)
    grain: list[str] = Field(default_factory=list)
    verified: bool = False  # 人工确认标记
    restricted: bool = False  # 权限分区：受限层不进低权限用户上下文（6.2-3）

    def caliber_text(self) -> str:
        """完整口径文本（注入 agent 上下文与口径声明）。"""
        if self.numerator is None:
            return f"{self.definition}；表达式参考：{self.expr}"
        parts = [self.definition]
        num = self.numerator
        num_src = f"{num.table} 表" if num.table else ""
        num_flt = f"，过滤 {num.filter}" if num.filter else ""
        parts.append(
            f"分子：{num.expr}（{num.description or '—'}；{num_src}{num_flt}）"
        )
        if self.denominator is not None:
            den = self.denominator
            den_src = f"{den.table} 表" if den.table else ""
            den_flt = f"，过滤 {den.filter}" if den.filter else ""
            parts.append(
                f"分母：{den.expr}（{den.description or '—'}；{den_src}{den_flt}）"
            )
        if self.time_field:
            parts.append(f"统计时间字段：{self.time_field}（分子分母口径一致）")
        return "；".join(parts)


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
