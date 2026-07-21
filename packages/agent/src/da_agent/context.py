"""语义上下文构建（架构文档 6.1 第三层：语义层按权限裁剪后注入）。

低权限用户的上下文中不包含无权对象与受限口径——被问到时自然回答"没有找到相关数据"。
"""

from __future__ import annotations

from da_connectors.base import Connector
from da_semantic import Entity, Metric, SemanticStore
from da_types import CatalogSnapshot, DataObject, UserIdentity

SYSTEM_PROMPT_TEMPLATE = """你是一名严谨的企业数据分析师。\
用户会用自然语言提问，你通过 run_sql 工具查询数据并给出分析结论。

## 可用数据表
{schema_section}

## 业务语义层（口径以此为准，不要自行猜测）
{semantic_section}

## 工作规则
1. 先想清楚口径，再写 SQL；指标定义必须遵循语义层，不得改口径。
2. SQL 方言：{dialect}。查询是只读的，系统会强制注入 LIMIT，聚合查询不受影响。
3. 查询报错时，阅读错误信息并修正 SQL 重试。
4. 数据不足以回答时明确说"数据不足"，禁止编造数字（宁可拒答，不给错数）。
5. 你没有见到的表不存在，不要引用。

## 回答格式（四件套）
最终回答必须包含：
- **结论**：直接回答问题，含关键数字
- **口径说明**：本次计算采用的口径（引用语义层定义）
- **数据摘要**：支撑结论的关键数据
- **建议**（如适用）：一句话的下一步建议
"""


def render_schema(catalog: CatalogSnapshot, allowed_databases: set[str]) -> str:
    lines = []
    for t in catalog.tables:
        if t.database not in allowed_databases:
            continue  # 权限裁剪：无权对象不进上下文
        cols = ", ".join(
            f"{c.name} {c.type}".strip() + (f" /*{c.comment}*/" if c.comment else "")
            for c in t.columns
        )
        lines.append(f"- {t.name}({cols})  -- 约 {t.row_count} 行")
    return "\n".join(lines) if lines else "（无可用表）"


def render_semantics(
    entities: list[Entity], metrics: list[Metric], identity: UserIdentity
) -> str:
    lines = []
    for m in metrics:
        if m.restricted:
            continue  # 受限层口径不进低权限上下文（M1：restricted 一律裁剪）
        mark = "✓已确认" if m.verified else "草稿"
        lines.append(f"- 指标[{m.name}]（{mark}）：{m.caliber_text()}")
    for e in entities:
        alias = "/".join(e.aliases) if e.aliases else e.name
        # 漂移冻结的绑定不进上下文（宁可少答，不带病运行）
        active = [
            b for b in e.bindings
            if f"{b.table}.{b.column}" not in set(e.frozen_bindings)
        ]
        if not active:
            continue
        bind = ", ".join(
            f"{b.table}: {b.expr}（转换自 {b.column}）" if b.expr
            else f"{b.table}.{b.column}"
            for b in active
        )
        lines.append(f"- 实体[{e.name}]（别名：{alias}）：物理绑定 {bind}")
        for jp in e.join_paths:
            lines.append(f"  - 关联路径：{jp.expr}（置信度 {jp.confidence}）")
        for em in e.enum_mappings:
            for col, mapping in em.mappings.items():
                pairs = ", ".join(f"{k}={v}" for k, v in mapping.items())
                lines.append(f"  - 枚举[{em.concept}] {col}: {pairs}")
        for sr in e.semantic_roles:
            lines.append(f"  - 语义角色：{sr.table}.{sr.column} = {sr.role}")
    return "\n".join(lines) if lines else "（语义层为空）"


async def build_system_prompt(
    connector: Connector,
    semantic_store: SemanticStore,
    catalog: CatalogSnapshot,
    identity: UserIdentity,
) -> str:
    databases = {t.database for t in catalog.tables}
    decision = await connector.check_access(
        identity, [DataObject(database=db, table="*") for db in databases]
    )
    allowed = {o.database for o in decision.allowed_objects()}

    # 语义层权限裁剪（6.1 第三层）：无权用户连"表存在"这个事实也不该看到。
    # M1 粒度：用户对任何库无权限时，语义层整体不注入。
    if allowed:
        entities = [
            Entity.model_validate((await semantic_store.get("entity", n)).payload)
            for n in await semantic_store.list_names("entity")
        ]
        metrics = [
            Metric.model_validate((await semantic_store.get("metric", n)).payload)
            for n in await semantic_store.list_names("metric")
        ]
    else:
        entities, metrics = [], []

    return SYSTEM_PROMPT_TEMPLATE.format(
        schema_section=render_schema(catalog, allowed),
        semantic_section=render_semantics(entities, metrics, identity),
        dialect=connector.dialect,
    )
