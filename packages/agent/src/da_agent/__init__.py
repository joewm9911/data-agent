"""分析引擎：问答四件套 agent loop、指标树归因、playbook、统计守门员、主动层、报告。"""

from da_agent.agent import Answer, DataAnalystAgent, ExecutedSQL
from da_agent.config import LLMConfig, load_dotenv
from da_agent.llm import LLMClient
from da_agent.metric_tree import AttributionReport, MetricNode, MetricTreeEngine
from da_agent.playbooks import (
    PlaybookEngine,
    PlaybookRegistry,
    PlaybookSpec,
    channel_review_playbook,
    cx_ticket_anomaly_playbook,
)
from da_agent.proactive import Briefing, MonitorSpec, ProactiveMonitor
from da_agent.report import Report, render_answer_report, render_briefing_report

__all__ = [
    "Answer",
    "AttributionReport",
    "Briefing",
    "DataAnalystAgent",
    "ExecutedSQL",
    "LLMClient",
    "LLMConfig",
    "MetricNode",
    "MetricTreeEngine",
    "MonitorSpec",
    "PlaybookEngine",
    "PlaybookRegistry",
    "PlaybookSpec",
    "ProactiveMonitor",
    "Report",
    "channel_review_playbook",
    "cx_ticket_anomaly_playbook",
    "load_dotenv",
    "render_answer_report",
    "render_briefing_report",
]
