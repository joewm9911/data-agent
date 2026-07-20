# data-agent

企业数据分析 Agent：接入企业已有数据体系（数仓 / ClickHouse / 数据中台 / BI 资产），以自然语言完成「查数 → 归因 → 主动洞察 → 交付报告」的完整分析闭环。

## 文档

- [完整架构设计方案](docs/architecture-design.md) — 产品定位、六大平面模块设计、部署态、工程规范、北极星指标与实施阶段

## 仓库结构

```
packages/
  core-types/   # 跨模块共享契约（Query IR / UserIdentity / GuardPolicy …）
  platform/     # 基础设施抽象层：lease/队列/pubsub/blob 最小原语 + provider 实现
  governance/   # 治理平面：护栏（只读/LIMIT/最小聚合HAVING/注入中和）+ 全链路审计
  connectors/   # 接入层：四接口抽象 + CK/SQLite 适配器 + conformance 套件
                #   + dbt manifest 导入器 + MCP 适配器桥
  semantic/     # 语义层引擎：模型/版本化存储/查询日志挖掘/profiling/证据图实体归一
                #   /确认队列/学习回路/冷启动流水线/开放格式导出
  agent/        # 分析引擎：问答四件套 loop/指标树归因/playbook/统计守门员/主动层/报告
  runtime/      # 运行时：回合队列 worker/会话串行锁/快照水合/controller 生命周期
  evals/        # Eval harness：golden 场景/判分/准确率仪表盘/回归门槛
apps/
  api/          # 交付层 API：对话回合/报告/管理控制台（权限/确认队列/审计/仪表盘）
tests/          # 60+ 测试：确定性单测/集成测 + live LLM 端到端（无 key 自动跳过）
examples/       # demo_cx.py：真实 CX 场景端到端演示（MiniMax 驱动）
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

架构文档特性全量落码，97 项测试（含真实 Postgres/Redis provider 测试与 live LLM e2e）：

- **持久化**：Postgres 语义层/审计存储（DDL 在源码）、Redis lease/queue/pubsub、文件系统 BlobStore
- **交付**：SSE 流式对话、Bearer 鉴权（IdentityProvider）、内嵌 Web 前端（`GET /`）、SVG 图表、
  定时晨报调度器、IM webhook（飞书/钉钉/企微/Slack）
- **语义飞轮**：查询日志挖掘、证据图归一、LLM 语义增强（列描述/枚举含义/文档挖掘，MiniMax）、
  verified answers 模糊召回（数字守卫）、schema 漂移冻结、指标树自动草稿
- **治理**：熔断/租户配额、差分审计检测、最小聚合 HAVING、注入中和、全链路审计
- **归因**：维度分解 + 乘法因子分解 + 递归下钻，playbook 与归因引擎均为 LLM 可调用工具
- **运行时**：回合检查点、心跳自动续租、会话休眠/水合、token 成本统计
- **部署**：`deploy/` docker-compose（单机 all-in-one）+ K8s manifests + Dockerfile

启动：`uv run uvicorn da_api.main:app`（需 `.env`）；演示：`uv run python examples/demo_cx.py`。
