"""真实场景：电商 + 客服（CX）业务数据集。

刻意还原企业数据的真实混乱度（架构文档 4.3 要测的东西）：
- 同一业务实体在三张表叫三个名字：orders.cust_no / crm_contacts.client_code / cs_tickets.customer_id
- 状态用数字编码（stat: 1/2/3），渠道用缩写（chan: 'tb'/'jd'/'dy'/'web'）
- 含测试账号（TEST 前缀），正确口径必须排除
- 7 月抖音渠道刻意注入"退款咨询"工单尖峰（供"为什么"类问题归因）

golden 答案由纯 SQL 独立计算，与 agent 回答对照（eval as test，8.3）。
"""

from __future__ import annotations

import random
import sqlite3
from datetime import date, timedelta
from pathlib import Path

from da_semantic import (
    Binding,
    Entity,
    EnumMapping,
    JoinPath,
    Metric,
    SemanticRole,
    SemanticStore,
)

CHANNELS = ["tb", "jd", "dy", "web"]
CHANNEL_WEIGHTS = [0.35, 0.25, 0.25, 0.15]
REGIONS = ["华东", "华南", "华北", "西南"]
TICKET_CATEGORIES = ["物流咨询", "退款咨询", "商品质量", "账号问题"]


def seed_database(db_path: str | Path, seed: int = 42) -> None:
    rng = random.Random(seed)
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        DROP TABLE IF EXISTS orders;
        DROP TABLE IF EXISTS crm_contacts;
        DROP TABLE IF EXISTS cs_tickets;
        CREATE TABLE orders (
            order_no TEXT PRIMARY KEY,
            cust_no TEXT,
            order_amt REAL,
            stat INTEGER,          -- 1=paid 2=pending 3=refunded
            pay_dt TEXT,
            chan TEXT
        );
        CREATE TABLE crm_contacts (
            client_code TEXT PRIMARY KEY,
            client_name TEXT,
            region TEXT,
            signup_dt TEXT
        );
        CREATE TABLE cs_tickets (
            tid INTEGER PRIMARY KEY,
            customer_id TEXT,
            cat TEXT,
            created_at TEXT,
            csat_score INTEGER,
            resolved INTEGER
        );
        """
    )

    # 客户：500 真实 + 10 测试账号
    customers = []
    for i in range(500):
        code = f"C{i:05d}"
        customers.append((code, f"客户{i}", rng.choice(REGIONS), "2025-01-01"))
    for i in range(10):
        customers.append((f"TEST{i:03d}", f"测试账号{i}", "华东", "2025-01-01"))
    conn.executemany("INSERT INTO crm_contacts VALUES (?,?,?,?)", customers)

    # 订单：2026-05-01 ~ 2026-07-19，约 3000 单
    start = date(2026, 5, 1)
    days = (date(2026, 7, 19) - start).days + 1
    orders = []
    for i in range(3000):
        d = start + timedelta(days=rng.randrange(days))
        cust = rng.choice(customers)[0]
        chan = rng.choices(CHANNELS, weights=CHANNEL_WEIGHTS)[0]
        amt = round(rng.uniform(30, 800), 2)
        stat = rng.choices([1, 2, 3], weights=[0.82, 0.08, 0.10])[0]
        orders.append((f"O{i:06d}", cust, amt, stat, d.isoformat(), chan))
    conn.executemany("INSERT INTO orders VALUES (?,?,?,?,?,?)", orders)

    # 工单：6 月基线 ~200 单；7 月抖音渠道退款咨询尖峰 +120 单
    tickets = []
    tid = 0
    for _ in range(200):
        d = date(2026, 6, 1) + timedelta(days=rng.randrange(30))
        tid += 1
        tickets.append(
            (tid, rng.choice(customers)[0], rng.choice(TICKET_CATEGORIES),
             d.isoformat(), rng.randint(2, 5), 1)
        )
    for _ in range(180):
        d = date(2026, 7, 1) + timedelta(days=rng.randrange(19))
        tid += 1
        tickets.append(
            (tid, rng.choice(customers)[0], rng.choice(TICKET_CATEGORIES),
             d.isoformat(), rng.randint(2, 5), rng.randint(0, 1))
        )
    for _ in range(120):  # 尖峰：7 月退款咨询
        d = date(2026, 7, 5) + timedelta(days=rng.randrange(10))
        tid += 1
        tickets.append(
            (tid, rng.choice(customers)[0], "退款咨询", d.isoformat(), rng.randint(1, 3), 0)
        )
    conn.executemany("INSERT INTO cs_tickets VALUES (?,?,?,?,?,?)", tickets)
    conn.commit()
    conn.close()


async def seed_semantics(store: SemanticStore, actor: str = "bootstrap") -> None:
    """语义层种子：模拟冷启动 + 人工确认后的状态。"""
    customer = Entity(
        name="客户",
        canonical_key="customer_id",
        aliases=["会员", "买家", "用户"],
        bindings=[
            Binding(table="orders", column="cust_no", grain="order"),
            Binding(table="crm_contacts", column="client_code", grain="customer"),
            Binding(table="cs_tickets", column="customer_id", grain="ticket"),
        ],
        join_paths=[
            JoinPath(expr="orders.cust_no = crm_contacts.client_code",
                     evidence="human", confidence=1.0),
            JoinPath(expr="cs_tickets.customer_id = crm_contacts.client_code",
                     evidence="human", confidence=1.0),
        ],
        enum_mappings=[
            EnumMapping(concept="订单状态",
                        mappings={"orders.stat": {"1": "已支付", "2": "待支付", "3": "已退款"}}),
            EnumMapping(concept="渠道",
                        mappings={"orders.chan": {"tb": "淘宝", "jd": "京东",
                                                   "dy": "抖音", "web": "官网"}}),
        ],
        semantic_roles=[
            SemanticRole(table="orders", column="pay_dt", role="支付日期"),
            SemanticRole(table="cs_tickets", column="created_at", role="工单创建日期"),
        ],
    )
    gmv = Metric(
        name="GMV",
        aliases=["成交额", "销售额"],
        definition=(
            "已支付订单（stat=1）金额汇总，按支付日期 pay_dt 归属，"
            "排除测试账号（cust_no 以 TEST 开头）"
        ),
        expr="SUM(order_amt) WHERE stat = 1 AND cust_no NOT LIKE 'TEST%'",
        grain=["day", "chan"],
        verified=True,
    )
    ticket_volume = Metric(
        name="工单量",
        definition="按 created_at 统计的客服工单数量",
        expr="COUNT(*) FROM cs_tickets",
        grain=["day", "cat"],
        verified=True,
    )
    await store.put("entity", "客户", customer.model_dump(), actor)
    await store.put("metric", "GMV", gmv.model_dump(), actor)
    await store.put("metric", "工单量", ticket_volume.model_dump(), actor)


def golden(db_path: str | Path, sql: str):
    """golden 答案独立计算：不经过任何产品代码。"""
    conn = sqlite3.connect(f"file:{Path(db_path)}?mode=ro", uri=True)
    try:
        return conn.execute(sql).fetchall()
    finally:
        conn.close()


GOLDEN_GMV_JUNE = (
    "SELECT ROUND(SUM(order_amt), 2) FROM orders "
    "WHERE stat = 1 AND cust_no NOT LIKE 'TEST%' "
    "AND pay_dt >= '2026-06-01' AND pay_dt <= '2026-06-30'"
)

GOLDEN_TOP_CHANNEL_JUNE = (
    "SELECT chan, ROUND(SUM(order_amt), 2) AS gmv FROM orders "
    "WHERE stat = 1 AND cust_no NOT LIKE 'TEST%' "
    "AND pay_dt >= '2026-06-01' AND pay_dt <= '2026-06-30' "
    "GROUP BY chan ORDER BY gmv DESC"
)

GOLDEN_JULY_TICKET_TOP_CAT = (
    "SELECT cat, COUNT(*) AS n FROM cs_tickets "
    "WHERE created_at >= '2026-07-01' GROUP BY cat ORDER BY n DESC"
)
