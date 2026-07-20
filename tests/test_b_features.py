"""B 类特性：图表 / 调度器 / IM webhook / playbook+verified 集成 / BI 解析器 / 熔断配额。"""

from datetime import UTC, datetime

import httpx
import pytest
from da_agent.charts import chartable, render_chart
from da_agent.notify import WebhookNotifier, format_payload
from da_connectors.importers.bi import (
    import_generic_dataset,
    import_metabase_card,
    import_superset_dataset,
    merge_imports,
)
from da_governance.breaker import (
    CircuitBreaker,
    CircuitOpenError,
    QuotaExceededError,
    RateQuota,
)
from da_runtime import SessionController, Turn
from da_runtime.scheduler import ScheduledJob, Scheduler
from da_types import ColumnSchema, QueryResult, UserIdentity

# ---- 图表 ----


def _qr(cols, rows):
    return QueryResult(columns=[ColumnSchema(name=c, type="") for c in cols], rows=rows)


def test_chart_bar_for_categories():
    r = _qr(["chan", "gmv"], [["淘宝", 1000.0], ["京东", 800.0], ["抖音", 600.0]])
    assert chartable(r) == "bar"
    svg = render_chart(r, "渠道GMV")
    assert svg.startswith("<svg") and "渠道GMV" in svg and svg.count("<rect") == 3


def test_chart_line_for_dates():
    rows = [[f"2026-07-{d:02d}", float(d)] for d in range(1, 15)]
    r = _qr(["day", "n"], rows)
    assert chartable(r) == "line"
    assert "<polyline" in render_chart(r)


def test_chart_none_for_wide_or_text():
    assert chartable(_qr(["a", "b", "c"], [[1, 2, 3]])) is None
    assert chartable(_qr(["a", "b"], [["x", "y"], ["z", "w"]])) is None


# ---- 熔断与配额 ----


def test_breaker_opens_on_consecutive_failures():
    clock = [0.0]
    b = CircuitBreaker(failure_threshold=3, cooldown_seconds=60, clock=lambda: clock[0])
    for _ in range(3):
        b.record(ok=False)
    assert b.is_open
    with pytest.raises(CircuitOpenError):
        b.check()
    clock[0] = 61.0  # 冷却期过 → 半开放行
    b.check()
    b.record(ok=False)  # 试探失败 → 立即重新 OPEN
    with pytest.raises(CircuitOpenError):
        b.check()


def test_breaker_opens_on_slow_queries():
    clock = [0.0]
    b = CircuitBreaker(slow_threshold_ms=1000, slow_count_threshold=2,
                       cooldown_seconds=60, clock=lambda: clock[0])
    b.record(ok=True, duration_ms=1500)
    assert not b.is_open
    b.record(ok=True, duration_ms=2000)
    assert b.is_open


def test_quota_sliding_window():
    clock = [0.0]
    q = RateQuota(max_queries=2, window_seconds=60, clock=lambda: clock[0])
    q.check("t1")
    q.check("t1")
    with pytest.raises(QuotaExceededError):
        q.check("t1")
    q.check("t2")  # 租户隔离
    clock[0] = 61.0
    q.check("t1")  # 窗口滑过恢复


# ---- BI 解析器 ----


def test_superset_dataset_import():
    imp = import_superset_dataset({
        "table_name": "orders",
        "description": "订单宽表",
        "sql": "SELECT * FROM raw.orders WHERE is_test = 0",
        "columns": [{"column_name": "cust_no", "verbose_name": "客户编号"}],
        "metrics": [{"metric_name": "gmv", "verbose_name": "GMV",
                     "expression": "SUM(order_amt)", "description": "成交额"}],
    })
    assert imp.table_docs["orders"] == "订单宽表"
    assert imp.column_docs[("orders", "cust_no")] == "客户编号"
    assert imp.metric_drafts["GMV"]["expr"] == "SUM(order_amt)"
    assert "is_test = 0" in imp.embedded_sql[0]  # 隐性口径进挖掘信号


def test_metabase_and_generic_merge():
    mb = import_metabase_card({
        "name": "月度GMV",
        "description": "按月成交",
        "dataset_query": {"native": {"query": "SELECT month, SUM(amt) FROM o GROUP BY 1"}},
        "result_metadata": [{"table_name": "o", "name": "amt", "display_name": "金额"}],
    })
    gen = import_generic_dataset({
        "table": "cs_tickets", "description": "工单表",
        "columns": [{"name": "cat", "label": "工单类型"}],
        "metrics": [{"name": "工单量", "definition": "工单数", "expr": "COUNT(*)"}],
    })
    merged = merge_imports(mb, gen)
    assert merged.metric_drafts["月度GMV"]
    assert merged.column_docs[("cs_tickets", "cat")] == "工单类型"
    assert len(merged.embedded_sql) == 1


# ---- IM webhook ----


def test_im_payload_formats():
    assert format_payload("feishu", "T", "B")["msg_type"] == "text"
    assert format_payload("slack", "T", "B")["text"].startswith("*T*")
    assert format_payload("dingtalk", "T", "B")["msgtype"] == "markdown"


async def test_webhook_notifier_posts():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = request.content.decode()
        return httpx.Response(200)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    notifier = WebhookNotifier("https://open.feishu.cn/hook/x", style="feishu",
                               client=client)
    ok = await notifier.send("晨报", "工单量异常")
    assert ok and "工单量异常" in captured["body"]
    await client.aclose()


# ---- 调度器 ----


async def test_scheduler_fires_interval_job_and_notifies():
    results = []

    async def executor(question, identity, session_id, history):
        return f"简报：{question}", list(history)

    controller = SessionController(executor=executor)

    async def on_result(job: ScheduledJob, text: str):
        results.append((job.name, text))

    sched = Scheduler(controller=controller, on_result=on_result)
    sched.add(ScheduledJob(
        name="工单晨报", turn_input="[晨报] 工单量检查", session_id="proactive-cx",
        identity=UserIdentity(tenant_id="t", user_id="system"),
        interval_seconds=3600,
    ))

    fired = await sched.tick(datetime(2026, 7, 20, 8, 0, tzinfo=UTC))
    assert fired == ["工单晨报"]
    assert results and "简报：[晨报] 工单量检查" in results[0][1]
    # 间隔未到不重复触发
    assert await sched.tick(datetime(2026, 7, 20, 8, 30, tzinfo=UTC)) == []
    assert await sched.tick(datetime(2026, 7, 20, 9, 1, tzinfo=UTC)) == ["工单晨报"]


async def test_scheduler_daily_job():
    async def executor(question, identity, session_id, history):
        return "ok", list(history)

    sched = Scheduler(controller=SessionController(executor=executor))
    sched.add(ScheduledJob(
        name="daily", turn_input="[晨报]", session_id="p1",
        identity=UserIdentity(tenant_id="t", user_id="system"), daily_at="08:00",
    ))
    assert await sched.tick(datetime(2026, 7, 20, 7, 59, tzinfo=UTC)) == []
    assert await sched.tick(datetime(2026, 7, 20, 8, 1, tzinfo=UTC)) == ["daily"]
    assert await sched.tick(datetime(2026, 7, 20, 9, 0, tzinfo=UTC)) == []  # 当日已发
    assert await sched.tick(datetime(2026, 7, 21, 8, 1, tzinfo=UTC)) == ["daily"]


# ---- proactive turn 模型健全性 ----


def test_turn_kind_proactive():
    t = Turn(session_id="s", kind="proactive", input_text="[晨报]")
    assert t.kind == "proactive"
