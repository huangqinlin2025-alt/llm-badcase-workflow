# Badcase 自动入库工作流：现状架构、路线对比与渐进演进建议

## 执行摘要

现有产物已经完成了可运行 Demo 的核心计算能力：导入 CSV/XLSX/企微表格、用启发式识别字段、按 `case_id` 合并评分、以四类信号筛选 badcase，并能通过 `wecom-cli` 写企微双子表。实际生产闭环尚未完成：静态前端不读取用户文件、不调用 API，审核只写浏览器内存；写库从第 0 行开始覆盖数据，且没有去重、增量更新、审核状态回写或失败补偿。

在 20 小时窗口内，推荐先实施“模块化 FastAPI 单体服务 + 复用现有领域纯函数 + SQLite 作业账本/Outbox + 企微表格适配器”。直接采用 LangGraph 能实现相同目标，但会把有限时间花在图状态、checkpointer、interrupt 恢复、线程隔离与副作用重放控制上，无法比轻量方案更快地补齐业务闭环。轻量化不是死路：只要现在固定领域契约、状态机、幂等键、审计和端口边界，后续可将编排器替换为 LangGraph，并逐步接入复杂权限和多文件智能匹配。

## 现有技术架构

当前工作区的代码并不在交付文档写的 `badcase_pipeline/` 子目录，而是位于仓库根目录；同时未发现文档所述的 `image_eval_workflow/`、`outputs/`、`eval_harness_v2/`。因此，应把当前目录视为“扁平化 Demo 交付包”，不能假设已有可复用的 LangGraph 工作流骨架、测试体系或部署工程。

实际架构分为四层。导入层由 `importer.py` 提供：本地 CSV/TSV/XLSX 读取、企微表格读取、表头启发式字段识别和规范化 `record` 抽取。领域层由 `pipeline.py` 提供：`align()` 按 `case_id` 合并记录，`judge()` 用绝对低分、人机分歧、机检/LLM 冲突、维度离群四类信号生成 badcase，`big_rows()` 和 `small_rows()` 映射企微表格行。编排层由 `run_pipeline.py` 和 `accept_to_small.py` 提供：前者运行导入至判定，并可写大库；后者从本地 JSON 快照按 `case_id` 选择后写小库。展示层 `dist/index.html` 仅用 `data.js` 的固定数据播放模拟日志和审核交互。

旧版 `engine.py` 是重要参考而不是当前主链路：它依赖一个硬编码的上游 JSONL 目录，具备硬伤、格式软违规、AB 显著劣势等扩展信号，已有以 `case_id + side` 去重的雏形，但这些能力尚未迁移到导入式管线。

### 已实现、缺口与风险

| 范围 | 已实现事实 | 当前缺口/风险 | 处理优先级 |
| --- | --- | --- | --- |
| 导入与解析 | CSV/TSV/XLSX 与企微表格；列名启发式识别；数组/分列评分解析 | 无大小、行数、内容、URL 访问控制；复杂 XLSX 支持有限；没有人工确认映射 | P0 |
| 对齐与判定 | `case_id` 对齐；四类信号；判定依据与严重度输出 | 仅按 `case_id` 合并，无法安全区别 A/B 或不同运行批次；规则和阈值未版本化；旧信号未迁移 | P0/P1 |
| 企微写入 | `wecom-cli` 参数数组调用；子表确保；分批写入 | 固定从第 0 行写入并含表头，可能覆盖历史审核数据；无读存量、去重、upsert、锁、重试或补偿 | P0 |
| 审核闭环 | 小库写入 CLI 存在 | 前端审核状态只存在内存；CLI 从本地 JSON 快照挑选 case，未验证大库实际状态；没有审核人、备注、版本控制 | P0 |
| 前端 | 展示样例、筛选、详情、审核界面 | 不是可用业务前端；上传仅显示文件名，未上传；日志为预置；导入字段未经统一转义直接进入 `innerHTML`，存在存储型 XSS 风险 | P0 |
| 工程治理 | 部分计算函数可复用 | 无 API、数据库、测试、配置分层、日志/监控、CI 或部署工件；文档和实际目录不一致 | P0/P1 |

## 节点与数据契约建议

建议保留原有六步业务含义，但将每个节点变为可独立测试、可重放的应用服务。`case_id`、来源标识、评分侧、模型/运行版本共同形成业务身份；不能只把 `case_id` 作为跨源合并或去重键。

| 节点 | 输入 | 输出 | 关键校验 | 失败语义 |
| --- | --- | --- | --- | --- |
| 导入登记 | 文件或受控企微链接、操作者、来源信息 | `ImportJob`、文件哈希、原始工件定位 | 白名单格式、大小/行数、来源 URL | 文件级失败，不写业务库 |
| 解析映射 | 原始二维表、映射规则版本 | `ImportedRecord[]`、解析报告、行级拒绝项 | 必需 ID、文本/分数字段、类型 | 坏行隔离；关键列缺失则任务失败 |
| 对齐 | `ImportedRecord[]` | `AlignedCase[]`、覆盖说明 | `case_id + side + run/model/version` 唯一性 | 不能确定的匹配进入待处理，禁止强合并 |
| 判定 | 对齐结果、规则版本、阈值版本 | `BadcaseCandidate[]`、每条信号证据 | 信号输入完整性、阈值有效性 | 信号异常为 `unknown/error`，不折算未命中 |
| 写入大库投影 | 候选、幂等键 | 企微远端行 ID、同步回执 | 写前比对、表头/schema、容量、批次 | 写入任务进入 Outbox，可重试、可对账 |
| 审核 | `badcase_id`、动作、用途、备注、版本 | 审核事件与状态转移 | 审核权限、乐观锁、合法状态机 | 冲突返回失败；不覆盖其他审核人决定 |
| 写入小库与回写 | 已采纳候选、审核事件 | 小库投影、已回写大库状态 | 先验状态为已采纳、外部行幂等 | 任一失败记录为待补偿，禁止伪报成功 |

建议的最小领域对象为：`ImportJob`、`ImportedRecord`、`AlignedCase`、`BadcaseCandidate`、`ReviewDecision`、`SheetProjection`、`OutboxTask`。每个候选应同时保存两种键：`candidate_key` 判断是否为同一业务对象；`content_hash` 判断同一对象内容是否变更。推荐候选键为 `source + case_id + side + evaluation_run/version`；缺少稳定 `case_id` 时才降级为规范化上下文哈希，并记录低置信度。

## 为什么 20 小时内先选轻量 FastAPI

### 路线 A：FastAPI/CLI + 现有纯函数

路线 A 不重写导入、解析、对齐与判定，只将其从 CLI 中提取为应用服务，再增加 API、审核命令、最小作业账本和企微同步适配器。现有 CLI 保留为回归入口和紧急人工补偿入口。建议新增依赖仅为 `fastapi`、`uvicorn` 和标准库 `sqlite3`；若团队已有稳定 PostgreSQL，也可直接用 PostgreSQL，但不应因建库运维侵蚀 20 小时 P0。

20 小时内可真实交付：受控文件导入、解析摘要和 badcase 返回、大库幂等投影、审核采纳/驳回 API、大库状态回写、小库写入、最小去重、失败 Outbox、基础 XSS 修复与冒烟测试。这正对齐当前 Demo 的核心缺口。

### 路线 B：直接 LangGraph + 持久化人工审核

LangGraph 可将 parse、align、judge、写大库、等待人工审核、写小库拆为图节点，并由 checkpointer 保存运行状态。它的长期价值在于多分支、循环、跨天人工等待、二次评测、LLM 归因和可回放的复杂编排。

但 LangGraph 的持久化不自动解决企微写入一致性。`interrupt()` 恢复时，所在节点会从头执行；若同一节点在暂停前进行了非幂等企微写入，恢复可能造成重复。即使用图状态，也仍必须实现业务幂等键、远端行定位、Outbox、审核状态机、权限和审计。因此在本项目当前阶段，LangGraph 是额外的编排复杂度，不是 P0 缺口的替代品。

### 时间与难度对比

以下估算基于一名熟悉 Python 的工程师、现有 `wecom-cli` 已可授权、固定目标文档可写，且不包含 SSO、HA、历史全量迁移与复杂 Excel 兼容。

| 维度 | A：轻量 FastAPI | B：直接 LangGraph |
| --- | ---: | ---: |
| 利用现有代码比例 | 85–95% | 45–65% |
| 20 小时后的现实产物 | 可用审核入库闭环 | 可演示的单机图原型，生产闭环风险仍高 |
| 导入/解析/判定接入 | 0.5–1 小时 | 4–6 小时（需定义节点 state） |
| 审核与恢复 | 5–7 小时 | 8–12 小时（interrupt、thread、checkpoint、恢复接口） |
| 幂等与企微状态回写 | 5–7 小时 | 8–13 小时（还须防重放副作用） |
| 最低测试工作量 | 3–5 小时 | 8–14 小时 |
| 初始技术难度（5 高） | 2/5 | 4/5 |
| 部署与调试难度（5 高） | 2/5 | 4/5，生产用 Postgres 时 5/5 |
| 完整生产化的额外投入 | 12–22 小时 | 30–55 小时 |
| 长期复杂流程扩展性 | 3/5 | 5/5 |

结论是：在“导入评分表 → 挖 badcase → 人工筛选 → 企微双库沉淀”这一固定线性流程内，路线 A 的业务价值/工时比显著更高。若 20 小时内强上路线 B，会出现“图能跑，但审核和外部写入仍不可信”的假闭环。

## 推荐的 20 小时切分

| 工作包 | 估时 | 验收结果 |
| --- | ---: | --- |
| 配置化与 API 骨架 | 1.5h | `DOCID`、子表 ID/名称、令牌、文件限制均由环境配置提供；健康检查可检验 CLI 可用性 |
| 领域 DTO、状态机、规则版本 | 1.5h | `ImportJob`、候选、审核决定、同步任务对象；状态可追溯 |
| `POST /api/import` | 3h | 上传 CSV/XLSX 或受控企微链接；返回解析报告、坏行和候选摘要 |
| 对齐/判定接入与映射确认 | 2h | 复用现有计算，新增 side/run 维度，输出证据与规则版本 |
| 大库 read–compare–upsert | 3.5h | 不再从第 0 行覆盖；保留人工列；重复提交不重复创建 |
| 审核 API 与大库回写 | 2.5h | `approve/reject` 持久化审核人、用途、备注；只能合法状态迁移 |
| 小库幂等写入与 Outbox 补偿 | 2h | 采纳项只写一次；部分失败可重试并可对账 |
| 前端接 API 与 XSS 修复 | 2h | 真实上传和审核结果；导入数据使用 `textContent` 或完整 HTML 转义 |
| 测试、冒烟、运行说明 | 2h | 覆盖重复导入、重复审核、企微失败、状态冲突和软违规单独命中 |

这 20 小时只承诺一个主格式路径与现有格式兼容，暂不承诺任意复杂 XLSX、多租户权限、自动多文件模糊匹配或 LangGraph 工作流。

## 轻量化之后如何演进

### 能否再引入 LangGraph

可以，且风险可控，前提是从第一个版本遵守“领域逻辑不依赖编排器”。将 `parse_input()`、`align_records()`、`judge_badcases()`、`project_to_big_sheet()`、`record_review()`、`project_to_small_sheet()` 设计成输入/输出稳定的应用服务；将 FastAPI 的顺序调用定义为 `DirectWorkflowRunner`。后续仅新增 `LangGraphWorkflowRunner`，将相同服务包装为节点，数据库仍为审核、候选、审计和 Outbox 的事实源，LangGraph checkpoint 只保存执行快照。

适合切换到 LangGraph 的触发条件是以下至少三项同时出现：审核等待通常超过 30 分钟且需跨天恢复；出现两段以上人工介入；入库之后需要 LLM 归因、修复建议和二次评测循环；需要并行 worker 和可回放节点级执行；团队已有 PostgreSQL 备份、监控和迁移能力。届时预计为已有轻量架构新增 24–40 小时；若现在没有固定契约而直接耦合 CLI/企微，迁移将上升至约 40–70 小时并伴随重写风险。

### 能否再引入复杂权限系统

可以。P0 不应做 SSO/RBAC 平台，但从第一天保留 `actor_id`、`actor_type`、`tenant/project_id`、`created_by`、`reviewed_by`、`audit_event` 字段；API 经过 `Authorization` 适配器，并在应用层执行“谁可导入、谁可审核、谁可查看”的授权检查。初期可使用单一服务令牌与显式审核员白名单；后续再接企业身份、OIDC/SSO、角色/资源/操作矩阵、审计导出。权限逻辑不能散落在前端或 `wecom-cli` 命令字符串中。

### 能否再引入多文件智能匹配

可以，但必须分两阶段。第一阶段先支持“多文件批次 + 显式 `case_id` 精确 join”，每个文件解析为相同 `ImportedRecord`，在同一 `import_batch_id` 内依据 `case_id + side + version` 合并。第二阶段才引入列映射模板、字段置信度和模糊匹配；模糊匹配只能产出 `needs_mapping_review`，不得自动写库。语义相似度、向量检索或 LLM 映射应只作为候选建议，不能绕过业务键、阈值和人工确认。

### 从第一天必须固化的技术规范

1. 领域模型、规则和状态机不导入 `fastapi`、`langgraph`、HTML 或 `wecom-cli`。
2. 企微为协作投影，不作为唯一业务事实源；至少使用本地 SQLite 保存任务、候选、审核事件、外部行 ID 和 Outbox。
3. 每次判定保存 `parser_version`、`rule_set_version`、阈值、输入哈希和证据快照。
4. 所有企微写入遵循“本地事务记录 → Outbox → 幂等外部调用 → 回执/对账”；禁止盲目重复 append。
5. 审核采用乐观锁和合法状态转移，禁止新导入把已审核记录重置为待审核。
6. 外部 CLI 只接受服务端固定命令数组；禁止把用户 URL、文件名或表格 ID 拼接为 Shell 命令。
7. 输入 URL 仅允许 HTTPS 和明确白名单域名；拒绝内网 IP、重定向到内网、超大响应和任意本地文件路径。
8. 所有导入文本在页面上使用 DOM `textContent` 或按上下文转义，禁止拼接未可信数据到 `innerHTML`。
9. 样例转换为测试语料，覆盖字段缺失、重复导入、A/B 同 case、信号边界、XSS 字符串、企微部分失败与审核冲突。

## 结论

原有方案的业务分层方向正确：输入标准化、对齐与多信号判定、企微双库、人工筛选高价值样本。问题不在于没有 LangGraph，而在于 Demo 的展示层、持久化事实、外部写入幂等和审核状态没有连接成可信闭环。

因此，应先用 20 小时将当前 Demo 转为最小可用服务：修复覆盖写入、实现持久审核、幂等投影和失败补偿，并让前端只展示真实后端结果。此方案不是对未来能力的放弃；通过稳定领域契约、数据库事实源和可替换工作流运行器，后续能够有序引入 LangGraph、复杂权限和多文件匹配，而不会推倒既有判定与企微集成核心。

## 参考资料

1. [LangGraph Persistence](https://docs.langchain.com/oss/python/langgraph/persistence)
2. [LangGraph Checkpointers](https://docs.langchain.com/oss/python/langgraph/checkpointers)
3. [LangGraph Interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts)
4. [FastAPI File Uploads](https://fastapi.tiangolo.com/tutorial/request-files/)
5. [FastAPI Security](https://fastapi.tiangolo.com/tutorial/security/)
