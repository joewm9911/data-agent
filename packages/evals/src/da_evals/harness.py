"""Eval harness（架构文档 8.3）：golden set 回归 + 客户可见的准确率报告。

EvalCase 的 golden 由纯 SQL 独立计算；判分策略：回答文本包含 golden 数字/关键词。
任何 prompt/模型/语义层变更 → 跑回归，准确率不回退才可发布（eval as test）。
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime


@dataclass
class EvalCase:
    case_id: str
    question: str
    # 期望出现在回答中的值（数字自动做千分位归一）；任一命中即通过可用 any_of
    expected: list[str]
    any_of: bool = False
    tags: list[str] = field(default_factory=list)


@dataclass
class CaseResult:
    case: EvalCase
    answer_text: str
    passed: bool
    matched: list[str] = field(default_factory=list)


@dataclass
class EvalReport:
    ran_at: datetime
    results: list[CaseResult] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def passed(self) -> int:
        return sum(1 for r in self.results if r.passed)

    @property
    def accuracy(self) -> float:
        return self.passed / self.total if self.total else 0.0

    def dashboard_markdown(self) -> str:
        """客户可见的准确率仪表盘（8.3：信任数字化 + 续约弹药）。"""
        lines = [
            "# 准确率仪表盘",
            f"- 运行时间：{self.ran_at.strftime('%Y-%m-%d %H:%M')} UTC",
            f"- 用例：{self.total}，通过：{self.passed}，准确率：**{self.accuracy:.0%}**",
            "",
            "| 用例 | 问题 | 结果 |",
            "|------|------|------|",
        ]
        for r in self.results:
            mark = "✅" if r.passed else "❌"
            lines.append(f"| {r.case.case_id} | {r.case.question[:40]} | {mark} |")
        return "\n".join(lines)


def _normalize(text: str) -> str:
    return re.sub(r"(?<=\d)[,，\s](?=\d)", "", text)


def judge(case: EvalCase, answer_text: str) -> CaseResult:
    text = _normalize(answer_text)
    matched = [e for e in case.expected if _normalize(e) in text]
    passed = bool(matched) if case.any_of else len(matched) == len(case.expected)
    return CaseResult(case=case, answer_text=answer_text, passed=passed, matched=matched)


async def run_evals(
    ask: Callable[[str], Awaitable[str]],
    cases: list[EvalCase],
) -> EvalReport:
    report = EvalReport(ran_at=datetime.now(UTC))
    for case in cases:
        answer_text = await ask(case.question)
        report.results.append(judge(case, answer_text))
    return report


def assert_no_regression(current: EvalReport, baseline_accuracy: float) -> None:
    """发布门槛：准确率不得低于基线（11.3 eval 即测试）。"""
    if current.accuracy < baseline_accuracy:
        raise AssertionError(
            f"eval 回归：准确率 {current.accuracy:.0%} < 基线 {baseline_accuracy:.0%}"
        )
