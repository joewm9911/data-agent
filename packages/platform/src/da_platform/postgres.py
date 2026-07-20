"""Postgres 连接助手（10.2）。所有 SQL/DDL 都在源码中，不依赖外部迁移工具。

上层存储实现（PgSemanticStore/PgAuditSink）通过本模块取连接；
DSN 经 DA_PG_DSN 环境变量配置，默认本机 data_agent 库。
"""

from __future__ import annotations

import os

import psycopg
from psycopg.rows import tuple_row

DEFAULT_DSN = "postgresql://localhost/data_agent"


def pg_dsn() -> str:
    return os.environ.get("DA_PG_DSN", DEFAULT_DSN)


def connect(dsn: str | None = None) -> psycopg.AsyncConnection:
    """返回未打开的异步连接协程；调用方 await 并负责关闭（或用 pool）。"""
    return psycopg.AsyncConnection.connect(dsn or pg_dsn(), row_factory=tuple_row)


async def ensure_schema(conn: psycopg.AsyncConnection, ddl: str) -> None:
    """幂等建表：DDL 必须全部使用 IF NOT EXISTS 语义。"""
    await conn.execute(ddl)
    await conn.commit()
