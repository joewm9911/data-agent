# data-agent

企业数据分析 Agent：接入企业已有数据体系（数仓 / ClickHouse / 数据中台 / BI 资产），以自然语言完成「查数 → 归因 → 主动洞察 → 交付报告」的完整分析闭环。

## 文档

- [完整架构设计方案](docs/architecture-design.md) — 产品定位、六大平面模块设计、部署态、工程规范、北极星指标与实施阶段

## 仓库结构

```
packages/
  core-types/   # 跨模块共享契约（Query IR / UserIdentity / GuardPolicy …）
  platform/     # 基础设施抽象层：lease/队列/pubsub/blob 最小原语 + provider 实现
  governance/   # 治理平面：查询护栏（sqlglot）+ 全链路审计
  connectors/   # 接入层：Connector 四接口 + ClickHouse 适配器（含 system.query_log 挖掘入口）
  semantic/     # 语义层引擎：业务概念模型 + 版本化存储
  runtime/      # Agent 运行时：回合模型 + 会话串行锁（fencing token）
  evals/        # Eval harness（M1 落地）
tests/          # 跨包测试（护栏语义 / lease 一致性 / 版本化 / 适配器契约）
```

## 开发

```bash
uv sync              # 安装全部 workspace 包与开发依赖
uv run pytest        # 测试
uv run ruff check .  # lint
uv run lint-imports  # 架构依赖方向检查（分层单向依赖）
```

依赖方向（架构文档第 2 章，CI 强制）：`runtime → semantic → connectors → governance → platform → core-types`。

## 当前阶段

M0（地基）：monorepo 骨架、core-types 契约、Connector 抽象 + CK 适配器、语义层版本化存储、会话串行锁、审计链。详见架构文档第 13 章。
