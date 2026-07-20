"""熔断与配额（3.4 护栏承诺的另一半："agent 永远打不挂你的集群"）。

- CircuitBreaker：连续失败/慢查询超阈值 → OPEN（冷却期内直接拒绝）→ 半开试探恢复
- RateQuota：租户级滑动窗口限额
时间源可注入（测试确定性）。
"""

from __future__ import annotations

import time
from collections import defaultdict, deque
from collections.abc import Callable
from dataclasses import dataclass, field


class CircuitOpenError(Exception):
    """熔断打开：数据源处于保护期，拒绝新查询。"""


class QuotaExceededError(Exception):
    """租户配额用尽。"""


@dataclass
class CircuitBreaker:
    failure_threshold: int = 5          # 连续失败次数
    slow_threshold_ms: float = 30_000   # 慢查询判定
    slow_count_threshold: int = 3       # 窗口内慢查询次数
    cooldown_seconds: float = 60.0
    clock: Callable[[], float] = time.monotonic

    _consecutive_failures: int = 0
    _slow_times: deque = field(default_factory=lambda: deque(maxlen=16))
    _opened_at: float | None = None

    def check(self) -> None:
        """执行前调用；OPEN 且未过冷却期时抛 CircuitOpenError。"""
        if self._opened_at is None:
            return
        if self.clock() - self._opened_at >= self.cooldown_seconds:
            # 半开：放行一次试探（失败会立即重新 OPEN）
            self._opened_at = None
            self._consecutive_failures = self.failure_threshold - 1
            return
        raise CircuitOpenError("数据源熔断保护中，请稍后重试")

    def record(self, ok: bool, duration_ms: float = 0.0) -> None:
        if not ok:
            self._consecutive_failures += 1
            if self._consecutive_failures >= self.failure_threshold:
                self._opened_at = self.clock()
            return
        self._consecutive_failures = 0
        if duration_ms >= self.slow_threshold_ms:
            now = self.clock()
            self._slow_times.append(now)
            recent = [t for t in self._slow_times if now - t <= self.cooldown_seconds]
            if len(recent) >= self.slow_count_threshold:
                self._opened_at = now

    @property
    def is_open(self) -> bool:
        return self._opened_at is not None


@dataclass
class RateQuota:
    max_queries: int = 120
    window_seconds: float = 60.0
    clock: Callable[[], float] = time.monotonic
    _hits: dict[str, deque] = field(default_factory=lambda: defaultdict(deque))

    def check(self, tenant_id: str) -> None:
        now = self.clock()
        hits = self._hits[tenant_id]
        while hits and now - hits[0] > self.window_seconds:
            hits.popleft()
        if len(hits) >= self.max_queries:
            raise QuotaExceededError(
                f"租户 {tenant_id} 超出配额（{self.max_queries} 次/{self.window_seconds:.0f}s）"
            )
        hits.append(now)
