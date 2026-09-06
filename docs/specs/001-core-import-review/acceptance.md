# SPEC-001：验收记录

- 状态：冻结中，待实现验收
- 版本：1.0
- 日期：2026-09-06

## 规格冻结确认

| 项目 | 结论 | 证据 |
| --- | --- | --- |
| 发布静态体验页面 | 通过 | GitHub Pages 工作流运行 `33977994647` 成功 |
| SQLite 事实账本 | 已确认 | 用户确认允许引入本地 SQLite |
| 多文件精确合并 | 已确认 | 用户确认首期采用显式 `case_id` 精确合并 |
| 企微读写边界 | 已确认 | 仅机器人创建且预检通过的固定文档可写 |
| 首期方法论 | 已确认 | 采用 SDD，先主体框架后功能升级，GitHub 负责版本与回滚 |

## ARC-001 验收记录

- 状态：通过
- 分支：`feat/arc-001-core-framework`
- 验证命令：`/Users/huagnqinlin/.workbuddy/binaries/python/versions/3.13.12/bin/python3 -m unittest discover -s tests -v`
- 结果：3 项测试通过；领域层静态检查未发现 `fastapi`、`subprocess`、`wecom-cli` 或 `langgraph` 依赖。
- 已实现：`app/` 分层骨架、环境配置、`/healthz`、安全运行状态、依赖声明和无敏感值的 `.env.example`。
- 已知限制：SQLite 迁移、真实数据库健康检查和企微预检属于 `DATA-001`/`WECOM-001`，当前健康响应会明确标记为待初始化或不可用。

## DATA-001 验收记录

- 状态：通过
- 分支：`feat/data-001-sqlite-outbox`
- 验证命令：`/Users/huagnqinlin/.workbuddy/binaries/python/versions/3.13.12/bin/python3 -m unittest discover -s tests -v`
- 结果：8 项测试通过。
- 已实现：前向迁移 `001_initial.sql`、导入批次/来源/问题/对齐/候选/审核/投影/Outbox/审计全表、迁移校验和、SQLite 事务仓储、请求幂等、候选终态保护、审核乐观锁、Outbox 查询和应用启动自动迁移。
- 已知限制：导入 API、来源文件持久化和 Outbox 工作器将在 `IMP-001`、`MERGE-001`、`WECOM-001` 中接入；本任务不执行任何企微调用。

## IMP-001 验收记录

- 状态：通过
- 分支：`feat/data-001-sqlite-outbox`
- 验证命令：`/Users/huagnqinlin/.workbuddy/binaries/python/versions/3.13.12/bin/python3 -W error::ResourceWarning -m unittest discover -s tests -v`
- 结果：13 项测试通过，未出现资源警告。
- 已实现：`POST /api/v1/import-batches` 真实 multipart 上传、CSV/TSV/简单 XLSX 白名单、上传大小/行数/单元格/XLSX ZIP 安全限制、显式 `side`/`evaluation_version` 校验、工件保存、解析报告、行级缺失 `case_id` 隔离、SQLite 来源/问题持久化、同幂等键同请求安全重放与不同请求冲突。
- 已知限制：企微链接读取将在 `WECOM-001` 接入；当前只完成本地文件导入，不生成对齐单元、候选或外部投影。

## 实现验收清单

- [x] 服务与健康检查可运行。
- [x] 数据库迁移可运行。
- [ ] 文件导入、精确合并、四类规则、解析/冲突报告正确。
- [ ] SQLite 记录批次、候选、审核、Outbox 和审计事件。
- [ ] 企微大库使用稳定键 upsert，不覆盖历史审核数据。
- [ ] 审核采纳/驳回真实持久化，采纳仅写入一次小库。
- [ ] 失败可对账和重试，服务重启不丢未完成任务。
- [ ] 前端仅呈现 API 真实结果，所有导入文本安全显示。
- [ ] 全部 P0 测试通过，运行手册和回滚步骤已验证。

## 变更规则

本规格的范围、合并键、状态机、规则阈值、事实源、企微写入策略或安全约束发生变化时，必须先更新 `requirements.md` 与 `design.md`，并补充测试和本记录的变更说明。
