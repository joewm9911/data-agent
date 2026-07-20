"""统计守门员（架构文档 5.4）：替业务用户挡住"被数据骗"。

不引 scipy——两比例 z 检验/正态近似手工实现，够用且零依赖。
"""

from __future__ import annotations

import math
from dataclasses import dataclass

SMALL_SAMPLE_N = 30


@dataclass
class StatWarning:
    kind: str
    message: str


def check_small_sample(n: int, label: str = "样本") -> StatWarning | None:
    if n < SMALL_SAMPLE_N:
        return StatWarning(
            kind="small_sample",
            message=f"{label}量仅 {n}（<{SMALL_SAMPLE_N}），差异可能是噪声，勿下强结论",
        )
    return None


def two_proportion_significance(
    success_a: int, total_a: int, success_b: int, total_b: int, alpha: float = 0.05
) -> tuple[bool, float, StatWarning | None]:
    """两比例 z 检验。返回 (是否显著, p值近似, 警告)。"""
    if total_a == 0 or total_b == 0:
        return False, 1.0, StatWarning(kind="no_data", message="分母为 0，无法检验")
    p1, p2 = success_a / total_a, success_b / total_b
    pooled = (success_a + success_b) / (total_a + total_b)
    se = math.sqrt(pooled * (1 - pooled) * (1 / total_a + 1 / total_b))
    if se == 0:
        return False, 1.0, None
    z = (p1 - p2) / se
    p_value = 2 * (1 - _phi(abs(z)))
    significant = p_value < alpha
    warning = None
    if not significant:
        warning = StatWarning(
            kind="not_significant",
            message=f"该差异在统计上不显著（p≈{p_value:.2f}），可能是随机波动",
        )
    return significant, p_value, warning


def check_mom_seasonality(
    base_label: str, current_label: str
) -> StatWarning | None:
    """环比提示：跨月对比提醒季节性/天数差异（M2 先规则化，M3 接真实节假日日历）。"""
    days = {"01": 31, "02": 28, "03": 31, "04": 30, "05": 31, "06": 30,
            "07": 31, "08": 31, "09": 30, "10": 31, "11": 30, "12": 31}
    b, c = base_label[-2:], current_label[-2:]
    if b in days and c in days and days[b] != days[c]:
        return StatWarning(
            kind="calendar",
            message=f"对比月天数不同（{days[b]} vs {days[c]} 天），环比需校准日均",
        )
    return None


def check_simpson(
    overall_delta: float, group_deltas: list[float]
) -> StatWarning | None:
    """辛普森悖论检测：整体方向与全部分组方向相反时提示。"""
    if not group_deltas or overall_delta == 0:
        return None
    groups_direction = {d > 0 for d in group_deltas if d != 0}
    if len(groups_direction) == 1:
        (group_up,) = groups_direction
        if group_up != (overall_delta > 0):
            return StatWarning(
                kind="simpson",
                message="整体趋势与所有分组趋势相反（辛普森悖论），大概率是结构占比变化所致",
            )
    return None


def _phi(x: float) -> float:
    """标准正态 CDF（erf 近似）。"""
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))
