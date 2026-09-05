# Badcase 自动入库工作流：SDD 实施概设、Issue 拆分与 20 小时排期

## 执行摘要

可以采用 SDD（Spec-Driven Development，规格驱动开发）推进本项目，并且它非常适合当前“先完成可回滚主体框架，再迭代细节和功能升级”的目标。首期以模块化 FastAPI 单体、SQLite 业务账本与 Outbox、既有解析/判定纯函数、企微双表投影为主体；不在 20 小时内引入 LangGraph、复杂 IAM 或模糊智能匹配。

当前工作区还不是 Git 仓库，尚无 GitHub 远程。第一项工程工作应是建立仓库、基线提交、`main` 分支保护和 SDD 文档目录；随后所有实现都由可审查的规格、技术设计、任务列表和验收测试驱动。每个可部署版本用语义化 Git tag 标记；应用可回滚到上一个 tag，SQLite 通过迁移版本和发布前备份保证数据可恢复。

## 已确认的业务决策与边界

首期允许引入本地 SQLite；支持同一批次内以显式 `case_id` 为键的精确多文件合并；固定企微双库由机器人读写。考虑到机器人通常不能写用户手工创建的文档，首期仅将机器人创建且已通过预检的固定文档作为写目标。任何来源链接都必须先验证机器人读取权限、文档类型、域名和 `docid`，预检失败时拒绝任务而不产生部分写入。

首期主体框架的目标是：操作员提交一个或多个评分文件，系统解析并将同一 `case_id` 的多路分数精确合并，按版本化规则产生候选 badcase，幂等同步到企微大库；审核员采纳或驳回后，系统回写大库，并仅将采纳项幂等同步到小库。SQLite 是候选、审核、幂等、外部行 ID、失败任务和审计事件的事实来源；企微表格是协作视图和外部投影，不再是唯一状态源。

以下内容不属于 20 小时范围：LangGraph 编排、企业 SSO/完整 RBAC、语义/LLM 模糊匹配、任意复杂 XLSX、全量历史数据迁移、高可用/多实例部署和消息队列。

## SDD 工作方式

SDD 的原则是“规格先于代码，规格变更带动实现与验收变更”。近一年中文工程实践同样强调将重心从直接修改代码转为演进规范；本项目将它落实为轻量、可执行的四层工件，而非增加无效文档负担。

每个需求按以下闭环推进：`需求规格 → 技术设计 → 可执行任务 → 测试与验收 → 变更记录`。代码实现开始前，必须存在已确认的规格；实现中需求变化，先更新对应规格与验收，再修改代码。小改动可合并在一个规格下；有业务边界变化的功能须新增规格或 ADR，而不直接堆在代码里。

建议仓库落地结构如下：

```text
badcase-workflow/
├── docs/
│   ├── specs/
│   │   ├── 001-core-import-review/
│   │   │   ├── requirements.md
│   │   │   ├── design.md
│   │   │   ├── tasks.md
│   │   │   ├── test-plan.md
│   │   │   └── acceptance.md
│   │   └── 002-multi-file-exact-merge/
│   ├── adr/
│   │   ├── 001-sqlite-as-system-of-record.md
│   │   ├── 002-wecom-as-projection.md
│   │   └── 003-direct-runner-before-langgraph.md
│   └── runbooks/
│       ├── rollback.md
│       ├── wecom-reconciliation.md
│       └── local-development.md
├── app/
│   ├── api/
│   ├── application/
│   ├── domain/
│   ├── infrastructure/
│   └── workers/
├── tests/
├── migrations/
├── scripts/
└── legacy/                         # 现有 Demo 仅做对照，逐步迁移
```

`requirements.md` 只定义用户可见行为、角色、范围、非目标、验收指标和异常规则；`design.md` 定义状态机、实体、API、表结构、端口、错误模型和安全限制；`tasks.md` 只列可开发、可测试的任务；`test-plan.md` 将验收条件转换为自动化和人工测试；`acceptance.md` 记录演示结果、证据和剩余已知限制。ADR 只记录不可轻易逆转的选择与原因，例如 SQLite 是否为事实源、企微是否只是投影、何时引入 LangGraph。

## 主体框架的目标架构

```text
静态前端 / CLI
       │ HTTPS（令牌、请求 ID、幂等键）
       ▼
FastAPI 入站适配器
       ▼
应用服务
  ImportBatchService / ReviewService / ReconcileService
       ▼
领域层（与框架无关）
  解析映射 / 精确合并 / 多信号判定 / 状态机 / 幂等决策
       ▼
端口接口
  Repository | ArtifactStore | WeComSheetGateway | Clock | ActorContext
       ▼
SQLite 业务账本 ─── Outbox Worker ─── wecom-cli ─── 机器人创建的企微双子表
```

现有 `importer.py` 中的读取、`detect_schema()`、`extract()` 可以迁为输入适配器；`pipeline.py` 的 `align()` 和 `judge()` 应迁为领域服务；`big_rows()`、`small_rows()` 与 `wecom-cli` 子进程调用应重构为 `WeComSheetGateway`；`run_pipeline.py` 和 `accept_to_small.py` 成为兼容 CLI 或调试入口。`dist/index.html` 只保留视觉交互，必须改为调用真实 API，不能继续把 `data.js` 或 `pipeline_result.json` 当作业务状态源。

### 首期实体与状态机

最小实体包括：`ImportBatch`、`SourceFile`、`ImportedRecord`、`AlignedCase`、`BadcaseCandidate`、`ReviewDecision`、`SheetProjection`、`OutboxTask`、`AuditEvent`。

导入任务状态为：`RECEIVED → PARSED → ALIGNED → DECIDED → PROJECTING_BIG → PENDING_REVIEW`；异常状态为 `FAILED` 或 `PARTIAL_FAILURE`。候选审核状态为：`PENDING_REVIEW → APPROVED | REJECTED`；只有 `APPROVED` 才能执行 `PROJECTING_SMALL → COMPLETED`。任何企微失败仅改变同步状态，不撤销审核决策；由 Outbox 继续重试或人工对账。禁止新一轮导入将既有 `APPROVED/REJECTED` 无条件重置为 `PENDING_REVIEW`。

多文件精确合并的规则为：同一 `import_batch_id` 中，以 `case_id + side + evaluation_version` 作为合并键；`side` 和 `evaluation_version` 缺失时视为显式校验失败，不自动猜测。每个来源文件可只含人工、LLM 或机检的一路；合并后允许覆盖不均，但缺失应标识为 `unknown`，不得当作未命中。相同键出现冲突分数或文本时，任务进入 `PARTIAL_FAILURE` 并生成可定位的冲突报告，不能任选一个覆盖。

## 企微与 SQLite 一致性规范

机器人写入的固定企微文档必须通过启动预检：机器人身份授权、目标 `docid`、大/小库子表 ID、表头版本和容量均符合预期。服务端配置这些值，客户端不能提交目标文档 ID。不得以“自动新建任意表”作为错误恢复措施。

每个 badcase 使用稳定的 `candidate_key`，建议由 `case_id + side + evaluation_version + rule_set_version` 构成；若同一业务对象内容变化，再通过 `content_hash` 判断更新。SQLite 保存 `candidate_key`、审核状态、远端大库/小库行 ID、同步版本和 Outbox 状态。企微写入使用“读取现存行 → 比对业务键 → 新增或更新自动字段”的 upsert，而不是当前从第 0 行覆盖式写入。人工列（审核状态、审核人、备注）只能由审核服务更新，导入同步不得覆盖。

Outbox 记录每一次外部操作，至少有 `operation_id`、`candidate_id`、目标表、操作类型、请求摘要、重试次数、最后错误与远端回执。网络超时属于“结果未知”：先按稳定键查询/对账，再决定是否重发，禁止直接再次 append。写大库、小库、回写审核状态没有跨系统事务，故以可重试补偿代替伪原子双写。

## 安全与质量规范

文件仅接收 CSV、TSV、简单 XLSX；限制 MIME、扩展名、未压缩大小、行数与单元格长度。解析 XLSX 时需限制 ZIP 解压大小与条目数，防止压缩炸弹。来源链接仅允许 HTTPS 和显式的企微文档域名；拒绝本地文件协议、内网 IP、重定向至内网、未识别 `docid` 或机器人无权限的链接。

`wecom-cli` 继续使用固定参数数组调用，设置超时并校验返回码；不使用 `shell=True`，不将用户输入拼入命令。令牌、`docid`、子表 ID 等从环境变量或本地不提交的配置读取，绝不写入前端、日志、提交历史或样例数据。

前端不得用 `innerHTML` 插入任何导入字段、证据、case ID、场景、列名或用户文本；使用 DOM API 和 `textContent`，或对每个 HTML 上下文执行正确转义。审核 API 必须验证请求令牌、操作人、状态版本和目标候选归属；首期可使用单一导入令牌与审核员白名单，保留 `actor_id`、`project_id`、`created_by`、`reviewed_by` 供后续 RBAC/SSO 迁移。

最低测试集包括：两类既有样例、三文件精确合并、缺 `case_id`、重复 case、同键冲突、部分评分覆盖、规则阈值边界、软违规单独命中、重复导入、重复审核、企微超时、企微部分成功、审核状态冲突、包含 HTML 的文本字段和服务重启后的 Outbox 恢复。

## Issue Backlog 与依赖

| Issue | 优先级 | 工作项 | 依赖 | 验收标准 |
| --- | --- | --- | --- | --- |
| `INF-001` | P0 | 初始化 Git、GitHub、忽略规则、基线提交、文档目录 | GitHub 仓库地址/访问权限 | 本地与远端一致；可从 tag 复原 |
| `SPEC-001` | P0 | 编写核心闭环规格、设计、测试计划和 ADR | 已确认业务边界 | 规格审阅通过，所有 P0 行为有验收条件 |
| `ARC-001` | P0 | 建立 `app/` 分层、配置加载、健康检查、依赖边界 | `SPEC-001` | 领域层不依赖 FastAPI/企微/LangGraph |
| `DATA-001` | P0 | SQLite schema、迁移、作业/候选/审核/Outbox 审计账本 | `ARC-001` | 重启后任务与审核状态保持；迁移可校验 |
| `IMP-001` | P0 | 导入 API、文件安全校验、解析与映射报告 | `ARC-001` | CSV/XLSX 可导入；坏行和关键列缺失有准确报告 |
| `MERGE-001` | P0 | 显式 `case_id` 精确多文件合并与冲突报告 | `DATA-001`、`IMP-001` | 三路文件可合并；冲突不自动覆盖 |
| `RULE-001` | P0 | 将现有四类信号封装为版本化领域规则 | `MERGE-001` | 现有样例结果回归；证据、阈值、规则版本可追溯 |
| `WECOM-001` | P0 | 预检、读取、幂等大库投影、Outbox 重试 | `DATA-001`、`RULE-001` | 不覆盖第 0 行历史；重复导入不重复写 |
| `REV-001` | P0 | 采纳/驳回 API、大库回写、小库幂等投影 | `WECOM-001` | 采纳/驳回持久化；小库只含采纳项；部分失败可补偿 |
| `UI-001` | P0 | 前端接 API、真实进度/错误、XSS 修复 | `IMP-001`、`REV-001` | 用户文件实际上传；页面不伪报企微写入 |
| `QA-001` | P0 | 单元/集成/冒烟测试、运行手册、对账脚本 | 上述 P0 | 主路径与失败路径可复现并可验收 |
| `RULE-002` | P1 | 迁移硬伤、软违规、AB 劣势信号 | `RULE-001` | 新旧回归差异可解释；软违规保持叠加语义 |
| `IAM-001` | P2 | OIDC/SSO、角色/资源/操作授权 | `REV-001` | 权限策略独立于业务规则 |
| `FLOW-001` | P2 | LangGraph Runner 与 checkpoint | `DATA-001`、`REV-001` | 可暂停/恢复且不重复企微副作用 |
| `MATCH-001` | P2 | 多文件映射模板、模糊匹配候选与人工确认 | `MERGE-001` | 仅精确键自动合并；模糊结果不自动入库 |

## 2.5 天 / 20 小时排期

### 第 1 天：8 小时 — 规格、框架和数据事实源

第 0.5 小时完成 `INF-001`：Git 初始化、`.gitignore`、基线提交与 GitHub 远程关联。第 1.5 小时完成 `SPEC-001`：冻结 `001-core-import-review` 的范围、API、状态机和验收。第 2 小时完成 `ARC-001`：搭起 FastAPI、配置、依赖注入、领域/基础设施边界。第 3 小时完成 `DATA-001` 的最小 SQLite 表、迁移、事务和 Outbox。第 1 小时完成现有导入/判定输出的回归快照，确保重构不改变已验证规则。

日终验收：服务可启动；配置不会泄露到前端；数据库迁移可重复执行；样例被读入并持久化为导入任务，不发生任何企微写入。

### 第 2 天：8 小时 — 主业务闭环

第 2 小时完成 `IMP-001`：真实文件上传、格式/大小校验、解析报告。第 1.5 小时完成 `MERGE-001`：多个文件按显式键精确合并并输出冲突。第 1.5 小时完成 `RULE-001`：四类现有规则版本化，证据与阈值写入候选。第 2 小时完成 `WECOM-001`：固定文档预检、读取既有行、按稳定键 upsert、Outbox。第 1 小时开始 `REV-001`：审核命令和合法状态机。

日终验收：三路文件能生成一份候选清单；重复导入不会产生重复候选；通过 Dry Run 可完整检查结果；真实企微写入仅在显式确认下进行，且不会从第 0 行覆盖历史行。

### 第 3 半天：4 小时 — 审核、前端接入和验收

第 1.5 小时完成 `REV-001`：采纳/驳回、大库回写、小库投影及失败补偿。第 1 小时完成 `UI-001` 的最小真实接入与全量导入字段的 XSS 修复。第 1 小时完成 `QA-001` 核心自动化测试与企微失败模拟。最后 0.5 小时完成运行手册、对账步骤和 `v0.1.0` 发布验收。

最终验收：以真实样例完成“上传 → 合并 → 判定 → 大库 → 采纳/驳回 → 小库”；刷新页面和服务重启后状态可恢复；同一提交重复执行无重复行；企微部分失败可定位并重试；所有 P0 规格的验收项有对应测试或演示证据。

## GitHub 与版本回滚规范

当前工作区尚未初始化为 Git 仓库，因此无法立即执行 GitHub 远程推送。首期需由项目所有者创建私有 GitHub 仓库并授权，或提供既有仓库 URL；初始化不应提交真实企微链接、授权信息、真实用户内容或 SQLite 生产数据。

采用 `main` 作为受保护发布分支，开发使用短生命周期 `feat/<issue>`、`fix/<issue>`、`docs/<issue>` 分支。每项 Issue 对应一个规格目录和一个 PR；PR 描述需链接规格、设计、测试证据与回滚影响。提交采用 Conventional Commits，例如 `feat(import): add exact multi-file merge`、`fix(wecom): preserve reviewer columns`。`main` 必须通过格式检查、单测、集成测试和敏感信息扫描后才能合并。

版本使用语义化标签：主体框架完成并可验收时为 `v0.1.0`；兼容功能增加为 `v0.2.0`；修复为 `v0.1.1`。每次发布生成 GitHub Release，包含规格变更、数据库迁移版本、部署步骤、已知限制、回滚说明和验证结果。

应用回滚是“部署上一稳定 tag + 运行兼容代码”；数据库不建议在生产环境依赖破坏性的 down migration。采用新增字段/表优先的可向后兼容迁移；发布前备份 SQLite 数据库；每次 migration 有版本号与校验；需要撤销业务结果时使用补偿事件或恢复备份，而不是删除审计记录。企微外部投影由对账任务根据 SQLite 的事实状态重放修正，因此代码回滚后仍能恢复双表一致性。

## 后续演进与触发条件

轻量框架跑通后可继续升级，不需要推倒重来。引入 LangGraph 时只新增 `LangGraphWorkflowRunner`，把既有应用服务包装成图节点；SQLite/未来 PostgreSQL 仍保存业务事实，checkpoint 只保存运行态。只有当审核跨天、存在多阶段人工审批、加入 LLM 归因/修复/二次评测循环、需要多 worker 恢复或需要节点级回放时，才启动 `FLOW-001`。预计在主体框架稳定后投入 24–40 小时；若届时需要生产多实例和 PostgreSQL，另计数据库运维与监控投入。

复杂权限系统可在现有 `ActorContext` 和审计字段基础上接 OIDC/SSO、RBAC 或策略引擎，不会侵入判定规则。多文件智能匹配先升级为更多显式 schema 映射模板；只有高质量标注样本、人工复核界面和可解释置信度建立后，才允许把模糊匹配作为“待确认候选”，而非自动写库。

## 结论

采用 SDD 是合适且推荐的：它会把当前 Demo 的不确定性转化为规格、验收和可回滚版本，而不是在重构时丢失业务规则。首期先建立模块化主体框架，完成真实双库审核闭环、精确多文件合并、SQLite 账本与 GitHub 可回滚发布；细节调优、扩展信号、LangGraph、复杂权限和智能匹配均通过后续规格逐步接入。

## 参考资料

1. [LangGraph Persistence](https://docs.langchain.com/oss/python/langgraph/persistence)
2. [LangGraph Interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts)
3. [FastAPI File Uploads](https://fastapi.tiangolo.com/tutorial/request-files/)
4. [FastAPI Security](https://fastapi.tiangolo.com/tutorial/security/)
5. [SDD(Spec Driven Development)规范驱动开发实践](https://weixin.sogou.com/link?url=dn9a_-gY295K0Rci_xozVXfdMkSQTLW6cwJThYulHEtVjXrGTiVgS00jIZXYHMADYb5l5qRHZaq89tFh02W7wVqXa8Fplpd9UKiFkR6Ugicoo3gdtietU2krcpPcYDDY71FnqMlBw-ovsQEqvg3ApO8uPmosAzvoQKNdiuXSlnb8nwAutaPK1M538QQVqxP7u_M5YkO0vC8h3WXbERb6SC5IEGahPfVnfqGy0NtJAPxqfg4E8s-uecXzHYJPr8CsuiV4qFRZvLRhlgeRt7bSzA..&type=2&query=Spec%20Driven%20Development%20SDD%20%E8%BD%AF%E4%BB%B6%E5%BC%80%E5%8F%91%E8%A7%84%E8%8C%83&token=E25487D9306200558D88D430F5144CA68EBE76BA6A9C2BD1)
