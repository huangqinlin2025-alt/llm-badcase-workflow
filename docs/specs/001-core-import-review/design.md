# SPEC-001：技术设计

- 状态：已冻结
- 版本：1.0
- 日期：2026-09-06

## 架构边界

```text
浏览器 / CLI
  → API 适配器（认证、请求校验、响应）
  → 应用服务（导入、审核、投影、对账）
  → 领域服务（解析映射、精确合并、判定、状态机）
  → 端口（Repository、ArtifactStore、WeComGateway、Clock）
  → SQLite / 本地工件 / wecom-cli
```

领域层不得导入 FastAPI、`subprocess`、`wecom-cli` 或 LangGraph。现有 `importer.py` 的解析能力迁入输入适配器；现有 `pipeline.py` 的 `align()`、`judge()` 迁入领域服务；企微读写与行映射迁入 `WeComGateway`。旧 CLI 仅保留为开发/回归入口。

## 模块边界

```text
app/
  api/                 # FastAPI 路由、DTO、鉴权
  application/         # ImportBatchService、ReviewService、ReconcileService
  domain/              # 实体、规则、精确合并、状态机、错误码
  infrastructure/      # SQLite、文件工件、wecom-cli、配置、Outbox worker
  workers/             # 投影重试与对账入口
migrations/            # 仅向前兼容迁移
legacy/                # 现有 Demo 对照代码，逐步迁移
```

## SQLite 事实模型

| 表 | 核心字段 | 目的 |
| --- | --- | --- |
| `import_batches` | `id`、`project_id`、`status`、`request_hash`、`created_by` | 追踪批次和请求幂等 |
| `source_files` | `id`、`batch_id`、`sha256`、`source_type`、`original_name` | 输入工件与文件去重 |
| `parse_issues` | `batch_id`、`file_id`、`row_no`、`code`、`detail` | 行级拒绝、映射问题和冲突 |
| `aligned_cases` | `id`、`batch_id`、`case_id`、`side`、`evaluation_version` | 精确合并后的业务单元 |
| `candidates` | `id`、`candidate_key`、`content_hash`、`rule_set_version`、`review_status`、`version` | badcase 事实与乐观锁 |
| `review_decisions` | `id`、`candidate_id`、`action`、`use`、`note`、`actor_id` | 不可变审核审计 |
| `sheet_projections` | `candidate_id`、`target`、`remote_row_id`、`state`、`last_error` | 大库/小库外部投影状态 |
| `outbox_tasks` | `id`、`operation`、`payload`、`state`、`attempts`、`next_attempt_at` | 可重试的外部副作用 |
| `audit_events` | `id`、`actor_id`、`type`、`request_id`、`payload_summary` | 追溯、诊断与合规 |

所有表使用外键、创建/更新时间和必要索引。`candidate_key` 唯一；`source_files.sha256` 在同一项目范围去重；审核更新以 `candidates.version` 做乐观锁。

## API 契约

所有写请求要求 `Authorization: Bearer <token>`、`X-Request-ID` 和 `Idempotency-Key`。相同幂等键+相同请求体返回原响应；相同键+不同请求体返回 `409`。

| 接口 | 行为 |
| --- | --- |
| `POST /api/v1/import-batches` | `multipart/form-data` 上传一个或多个文件及 `side/evaluation_version` 元数据；创建批次、解析、合并、判定并入队大库投影 |
| `GET /api/v1/import-batches/{id}` | 返回任务状态、解析报告、冲突、候选摘要和投影状态 |
| `GET /api/v1/candidates` | 分页查询候选；按批次、严重度、场景、审核状态筛选 |
| `GET /api/v1/candidates/{id}` | 返回候选、证据、来源、审核和投影详情 |
| `POST /api/v1/candidates/{id}/review` | 提交 `APPROVE` 或 `REJECT`、用途、备注、`expected_version`；采纳后入队小库和大库审核状态投影 |
| `POST /api/v1/reconciliation/run` | 仅运维令牌可调用；处理可重试 Outbox 并输出对账摘要 |
| `GET /healthz` | 校验配置、SQLite 和 `wecom-cli` 可调用性，不泄漏敏感配置 |

客户端不可提交目标 `docid`、子表 ID、命令参数或任意本地路径。

## WeCom Gateway

固定文档、子表、表头版本和容量来自服务端环境配置。启动预检必须验证：`wecom-cli` 可执行、机器人授权有效、固定文档可读、两个目标子表存在、表头满足版本、容量可用。任何预检失败均使写入接口不可用。

大库需增加内部稳定键列（例如 `candidate_key`）以支持查询/更新；若当前表不能扩展，则在 SQLite 保存行定位并将键编码到不可见/受控字段前，必须在 ADR 中重新评审。写入采用“读存量 → 比对 stable key → 新增或更新自动字段”，人工审核字段只由审核投影操作更新。

`subprocess.run()` 必须使用参数数组、超时、受控 PATH 和返回码校验。所有 CLI stdout 按第一个顶层 JSON 对象解析；日志只保存截断的错误摘要。

## Outbox 与失败恢复

业务状态与 Outbox 在同一个 SQLite 事务内写入。Worker 单线程运行，使用任务租约避免并发重复执行。每个操作具有 `operation_id` 和确定性幂等载荷。

- 网络超时：设为 `UNKNOWN_RESULT`，先读企微对账。
- 限流/临时失败：指数退避后设为 `RETRYABLE_FAILURE`。
- 权限/表头/容量错误：设为 `PERMANENT_FAILURE`，阻断相关业务步骤并提示运维。
- 写大库未完成：候选保持不可审核。
- 写小库或大库审核回写失败：审核决定保持真实，投影等待补偿；页面显示“已决定，待同步”，不得显示同步成功。

## 安全约束

文件类型、大小、行数、单元格长度、XLSX ZIP 条目和解压后大小在解析前校验。远程链接只允许 HTTPS 和企微允许域名；拒绝内网、重定向到内网、`file:`、非白名单域名和无 `docid` 链接。

令牌、企微 ID 和本地数据库不提交 Git；仅使用 `.env.example` 描述变量。前端以 `textContent` 填充任何导入字段，禁止把未可信字段拼接至 `innerHTML`。

## 可演进性

应用服务输入/输出为稳定 DTO，后续可由 `DirectWorkflowRunner` 替换为 `LangGraphWorkflowRunner`。LangGraph checkpoint 只存运行态，不能取代 SQLite 业务事实与 Outbox。身份上下文和 `project_id` 已预留给未来 OIDC/RBAC；多文件模糊匹配只能作为待人工确认的候选层，不能绕过精确合并规则。
