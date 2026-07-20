"""Connector 一致性测试套件（架构文档 3.1/11.2）：任何适配器过测才能注册。

用法（pytest 内）：
    results = await run_conformance(connector, identity_allowed, identity_denied)
    assert not results.failures
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from da_types import DataObject, GuardPolicy, MetadataScope, Query, TimeWindow, UserIdentity

from da_connectors.base import Connector, GuardRejectedError


@dataclass
class ConformanceResult:
    passed: list[str] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)

    def check(self, name: str, ok: bool, detail: str = "") -> None:
        (self.passed if ok else self.failures).append(
            name if ok else f"{name}: {detail}"
        )


async def run_conformance(
    connector: Connector,
    identity_allowed: UserIdentity,
    identity_denied: UserIdentity,
    sample_table: str | None = None,
) -> ConformanceResult:
    r = ConformanceResult()

    # C1: 元数据非空且结构完整
    catalog = await connector.get_metadata(MetadataScope())
    r.check("C1.metadata_nonempty", len(catalog.tables) > 0)
    r.check(
        "C1.columns_present",
        all(t.columns for t in catalog.tables),
        "存在无列的表",
    )

    table = sample_table or (catalog.tables[0].name if catalog.tables else "")

    # C2: 只读护栏——写语句必须被拒
    try:
        await connector.execute(
            Query(statement=f"DELETE FROM {table}", dialect=connector.dialect),
            identity_allowed,
            GuardPolicy(),
        )
        r.check("C2.write_rejected", False, "DELETE 未被拒绝")
    except GuardRejectedError:
        r.check("C2.write_rejected", True)
    except Exception as e:  # noqa: BLE001
        r.check("C2.write_rejected", False, f"错误类型不符: {type(e).__name__}")

    # C3: SELECT 可执行且 LIMIT 生效
    result = await connector.execute(
        Query(statement=f"SELECT * FROM {table}", dialect=connector.dialect),
        identity_allowed,
        GuardPolicy(max_result_rows=5),
    )
    r.check("C3.select_ok", result.columns is not None)
    r.check("C3.limit_enforced", len(result.rows) <= 5, f"返回 {len(result.rows)} 行")

    # C4: check_access——允许/拒绝身份行为正确
    objects = [DataObject(database=t.database, table=t.name) for t in catalog.tables[:3]]
    allowed_decision = await connector.check_access(identity_allowed, objects)
    denied_decision = await connector.check_access(identity_denied, objects)
    r.check("C4.allowed_identity", allowed_decision.all_allowed())
    r.check("C4.denied_identity", not denied_decision.all_allowed())

    # C5: 查询历史接口可调用（允许为空流，如 SQLite）
    window = TimeWindow(
        start=datetime.now(UTC) - timedelta(days=365), end=datetime.now(UTC)
    )
    _ = [hq async for hq in connector.get_query_history(window)]
    r.check("C5.query_history_callable", True)

    return r
