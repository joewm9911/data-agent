"""差分审计检测器（6.2-1 的另一半）：单条合规、组合越权的查询序列检测。

启发式：同一用户在窗口内对同一敏感表反复做聚合查询，且出现过小样本聚合结果
（可被差分反推个体），累计风险分超阈值 → 告警。
配合最小聚合行数（HAVING 强制）形成纵深：HAVING 防单次，本检测器防序列。
"""

from __future__ import annotations

import time
from collections import defaultdict, deque
from collections.abc import Callable
from dataclasses import dataclass, field

import sqlglot
from sqlglot import expressions as exp


@dataclass
class DifferentialAlert:
    tenant_id: str
    user_id: str
    table: str
    query_count: int
    small_results: int
    detail: str


@dataclass
class DifferentialAuditDetector:
    window_seconds: float = 600.0
    query_threshold: int = 5      # 窗口内对同一表的聚合查询次数
    small_result_rows: int = 5    # "小结果"判定（可反推个体的聚合桶）
    sensitive_tables: set[str] = field(default_factory=set)  # 空 = 全部表纳入
    clock: Callable[[], float] = time.monotonic
    # (tenant, user, table) -> deque[(ts, rows)]
    _events: dict = field(default_factory=lambda: defaultdict(deque))

    def observe(
        self,
        tenant_id: str,
        user_id: str,
        statement: str,
        dialect: str,
        result_rows: int,
    ) -> list[DifferentialAlert]:
        """每次成功执行后调用；返回新触发的告警（可为空）。"""
        tables = self._agg_tables(statement, dialect)
        now = self.clock()
        alerts = []
        for table in tables:
            if self.sensitive_tables and table not in self.sensitive_tables:
                continue
            key = (tenant_id, user_id, table)
            events = self._events[key]
            events.append((now, result_rows))
            while events and now - events[0][0] > self.window_seconds:
                events.popleft()
            small = sum(1 for _, rows in events if rows <= self.small_result_rows)
            if len(events) >= self.query_threshold and small >= 2:
                alerts.append(
                    DifferentialAlert(
                        tenant_id=tenant_id,
                        user_id=user_id,
                        table=table,
                        query_count=len(events),
                        small_results=small,
                        detail=(
                            f"{self.window_seconds:.0f}s 内对 {table} 发起 "
                            f"{len(events)} 次聚合查询，其中 {small} 次小样本结果，"
                            "存在差分推理越权风险"
                        ),
                    )
                )
                events.clear()  # 告警后重置窗口，避免重复刷屏
        return alerts

    @staticmethod
    def _agg_tables(statement: str, dialect: str) -> set[str]:
        try:
            tree = sqlglot.parse_one(statement, read=dialect)
        except sqlglot.errors.ParseError:
            return set()
        has_agg = any(True for _ in tree.find_all(exp.AggFunc))
        if not has_agg:
            return set()
        cte_names = {cte.alias_or_name for cte in tree.find_all(exp.CTE)}
        return {
            t.name for t in tree.find_all(exp.Table) if t.name not in cte_names
        }
