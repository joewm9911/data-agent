"""Eval harness：golden 场景 + 回归判分 + 准确率仪表盘（架构文档 8.3）。"""

from da_evals.harness import (
    CaseResult,
    EvalCase,
    EvalReport,
    assert_no_regression,
    judge,
    run_evals,
)

__all__ = [
    "CaseResult",
    "EvalCase",
    "EvalReport",
    "assert_no_regression",
    "judge",
    "run_evals",
]
