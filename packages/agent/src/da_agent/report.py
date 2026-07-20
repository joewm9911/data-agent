"""交付层（架构文档 5.6）：分析 → 可分享的 Markdown 报告。

分享可被接收者继续追问，追问以接收者权限重新执行（6.1）——报告只存 SQL 模板与结论，
不缓存明细数据。
"""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel, Field

from da_agent.agent import Answer
from da_agent.proactive import Briefing


class Report(BaseModel):
    report_id: str
    title: str
    markdown: str
    created_by: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    sql_templates: list[str] = Field(default_factory=list)
    restricted: bool = False


def render_answer_report(answer: Answer, author: str) -> Report:
    sqls = [e.statement for e in answer.executed if e.ok]
    md = "\n".join(
        [
            f"# 分析报告：{answer.question}",
            "",
            answer.text,
            "",
            "---",
            f"*生成于 {datetime.now(UTC).strftime('%Y-%m-%d %H:%M')} UTC · "
            f"执行 {len(sqls)} 条查询 · 追问将以你的数据权限重新执行*",
        ]
    )
    return Report(
        report_id=answer.turn_id,
        title=answer.question[:60],
        markdown=md,
        created_by=author,
        sql_templates=sqls,
    )


def render_briefing_report(briefing: Briefing, author: str = "system") -> Report:
    md = "\n".join(
        [
            f"# 晨报 · {briefing.monitor}",
            "",
            briefing.text,
        ]
    )
    sqls = briefing.attribution.evidence_sql if briefing.attribution else []
    return Report(
        report_id=f"brief-{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}",
        title=f"晨报 · {briefing.monitor}",
        markdown=md,
        created_by=author,
        sql_templates=sqls,
    )
