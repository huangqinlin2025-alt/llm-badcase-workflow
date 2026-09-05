# Badcase 自动入库工作流 · 交付文档

> 面向接手开发者 / IDE 的完整上下文。阅读本文后应能直接在现有代码上继续开发。
>
> 最后更新：2026-09-05 ｜ 代码位置：`badcase_pipeline/` ｜ 状态：Demo 可运行，已真实打通企业微信写入

---

## 1. 需求背景

### 1.1 业务场景

项目为「图生文（帮写）大模型 Prompt 冷启动评测」。评测流程会产生大量打分数据：

- **8 个业务场景**：聊天润色、恋爱助攻、高赞朋友圈、神评论、种草笔记、购物神评、短视频文案、全能帮写
- **规模**：1232 条用例 × 2 个模型侧（A/B）= **2464 次判分**
- **打分维度**：D1 格式合规 / D2 要求遵循 / D3 一致性 / D4 差异化 / D5 自然度，1–10 分两分一档
- **三路打分来源**：
  - 人工专家锚定（`anchors_*.jsonl`，最稀疏）
  - LLM-Judge 自动打分（`judge_a/b_*.jsonl`）
  - 机检规则校验（`d1_full_a/b.jsonl`，全量覆盖）

### 1.2 痛点（本工作流要解决的问题）

评测跑完后，人工需要：

1. 翻遍 **24+ 个散落的 jsonl 文件**（judge_a/b × 8 场景 + d1_full_a/b + anchors × 8）
2. 按 `case_id` 逐条交叉比对人工分 / LLM 分 / 机检违规
3. 主观判断哪些算 badcase、原因是什么、该怎么改
4. 手工复制粘贴进表格，整理成可评审的形式

**每轮评测都要完整重来一次** —— 高频、纯人力、极易疲劳漏判。这是典型的「确定性计算可自动化、主观判断需人工」的场景。

### 1.3 需求演进（重要：解释了为什么是现在这个形态）

| 阶段 | 需求 | 结果 |
| --- | --- | --- |
| 初始 | 自动监听打分产出，评测跑批时并行入库 | ❌ 已降级废弃 |
| **当前** | 用户主动导入打分文件（CSV/XLSX/企微链接），自动解析维度 | ✅ 已实现 |
| 当前 | 筛选结果先入**大库**，人工采纳后入**小库** | ✅ 已实现 |
| 当前 | 固定维护同一份企微文档，**不新建链接** | ✅ 已实现（同文档双子表）|

> **降级原因**：自动监听依赖常驻服务 + 文件系统 watch，部署与调试成本高；而实际使用中评测跑批是人工发起的批处理，"跑完手动导入"完全够用，且交互更可控。

---

## 2. 整体工作流（上游背景）

本模块是更大评测体系的一环。完整架构为「Skill + 双层递进 Loop」：

```
阶段1 人工前置建设（不可自动化）
  梳理业务场景/输入输出规范 → 定义指标体系与 Judge Prompt → 构建 Golden 与 Baseline → 人工标注锚定样本
        ↓
阶段2 Skill 层（固定执行流水线，无决策）
  ├─ 样本评测 Skill（内嵌「内层单样本迭代 Loop」：生成→打分→未达标则重写→记录 Trace）
  ├─ 一致性校验 Skill（抽样人机比对 → 计算 Cohen's Kappa）
  └─ Badcase 沉淀 Skill  ← ★ 本模块所在位置
        ↓
阶段3 双层递进 Loop（串行，不交叉不混跑）
  ├─ 第一层小 Loop（校准）：30–50 条小样本 → Kappa ≥0.6 准入，否则人工修正标准后重启
  └─ 第二层大 Loop（全量）：全量评测 → 指标报告 → 挖 Badcase → ★Badcase 子图★ → 线上验证 → 收敛
        ↓
阶段4 最终输出
  可复用工作流 + 可信 Judge 体系 + 扩充的 Golden 数据集 + Benchmark 报告 + 线上量化结论
```

**边界原则**：机器负责批量执行、计算校验、挖掘问题；人工只做标准定义、规则修改、样本审核、业务决策。

相关产物：
- 架构图（HTML 卡片版）：`outputs/生图评测_双层递进Loop架构图_卡片版.html`
- LangGraph 代码骨架（可运行）：`image_eval_workflow/`
- 可行性与工期评估：`outputs/Badcase自动入库_可行性与工期评估.md`

---

## 3. Badcase 入库工作流（本模块核心）

### 3.1 六步流程

```
① 导入 ──→ ② 解析 ──→ ③ 对齐 ──→ ④ 判定 ──→ ⑤ 入大库 ──→ ⑥ 人工审核 ──→ 小库
（人工）    （自动）    （自动）    （自动）    （自动）      （人工）
```

| 步骤 | 输入 | 处理 | 输出 | 代码位置 |
| --- | --- | --- | --- | --- |
| ① 导入 | CSV / XLSX / 企微表格链接 | 读取为 `(headers, rows)` | 原始二维表 | `importer.read_table` / `read_wecom_sheet` |
| ② 解析 | headers + rows | 启发式识别列语义 | `schema` + 标准化 `records` | `importer.detect_schema` / `extract` |
| ③ 对齐 | records | 按 `case_id` 归并多方打分 | `aligned` + 覆盖分布 | `pipeline.align` |
| ④ 判定 | aligned | 多信号检测 + 归因 + 分级 | `badcases` | `pipeline.judge` |
| ⑤ 入大库 | badcases | 构造行 + 分批写入企微 | 大库子表 | `pipeline.big_rows` / `push` |
| ⑥ 人工审核 | 大库记录 | 人工采纳/驳回 | 小库子表 | `accept_to_small.py` |

### 3.2 Step② 自动解析（本模块技术核心）

**设计目标：不依赖固定列名**。不同来源的打分表列名千差万别，硬编码列名会导致每换一份文件就要改代码。

识别规则（`importer.detect_schema`）：

| 目标 | 规则 |
| --- | --- |
| ID 列 | 列名含 `case_id`/`case`/`id`/`编号`/`用例`/`序号` |
| 场景列 | 含 `场景`/`scene`/`类型`/`category` |
| 上一轮输出 | 含 `上一轮`/`prev`/`原输出`/`上轮` |
| 用户要求 | 含 `要求`/`req`/`指令`/`需求` |
| 本轮输出 | 含 `本轮`/`输出`/`output`/`cur`/`结果` |
| 打分方 | 含 `人工`/`专家`/`human`/`标注` → 人工；`llm`/`judge`/`模型`/`ai` → LLM；`机检`/`machine`/`规则`/`校验` → 机检 |
| 维度（分列式） | 列名匹配 `^D\d` 或含「分」，且 ≥80% 取值为 0–10 数值 |
| 维度（数组式） | 整列取值匹配 `[8, 8, 9, 7, 8]` 形态，自动拆解为 N 维 |

**已验证的两种极端形态**：

| 样例文件 | 列名风格 | 识别结果 |
| --- | --- | --- |
| `打分结果_维度分列.csv` | 用例编号 / 业务场景 / 专家-格式分 / 模型打分-D1 / 规则校验得分 / 备注说明 | ✅ 17 列全部正确归类，自动忽略「备注说明」 |
| `打分结果_数组式.xlsx` | case / 场景分类 / 人工评分(D1-D5) / LLM评分(D1-D5) / 机检D1 | ✅ 数组字符串正确拆为 5 维 |

### 3.3 Step④ 判定信号

**为什么必须多信号**：实测发现单一信号完全不可用（详见 §5 关键发现）。

当前 `pipeline.judge` 实现的 4 类信号：

| 信号 | 触发条件 | 阈值键 |
| --- | --- | --- |
| 绝对低分 | 任一维度 ≤4，或综合均分 <6 | `low_dim` / `low_overall` |
| 人机分歧 | \|人工均分 − LLM均分\| ≥1.5 | `disagree` |
| 机检LLM冲突 | 机检均分 ≤6 但 LLM D1 ≥8 | — |
| 维度离群 | 某维度低于「同场景同打分方」均值 ≥2 | `outlier_gap` |

`engine.py`（旧版固定路径引擎）另实现了 **硬伤**、**AB显著劣势**、**格式软违规** 3 类，可按需迁移到 `pipeline.judge`。

**严重度分级**：≥3 个信号 → P0；2 个信号或含人机分歧 → P1；否则 P2

**建议动作（归因驱动）**：
- 含人机分歧 / 机检LLM冲突 → `修正Judge量表`（说明打分器本身有问题）
- 含绝对低分 → `候选Golden`
- 其他 → `补Few-shot`

### 3.4 Step⑤⑥ 双库设计

| | 大库 | 小库 |
| --- | --- | --- |
| 子表名 | `badcase大库(全量筛出)` | `精选badcase库(小库)` |
| sheet_id | `ROQRHs` | `Pmb4Gm` |
| 写入时机 | 判定命中 → **自动写入** | 人工审核**采纳后**写入 |
| 定位 | 问题全景 + 追溯底账 | 反哺模型/量表的高价值集合 |
| 列数 | 16 列 | 10 列 |
| 列结构 | case_id, 场景, 触发信号, 严重度, 打分覆盖, 建议动作, 人工均分, LLM均分, 机检分, 判定依据, 上一轮输出, 用户要求, 本轮输出, 审核状态, 审核人, 审核备注 | case_id, 场景, 严重度, 触发信号, 归因, 采纳用途, 上一轮输出, 用户要求, 本轮输出, 采纳时间 |

**关键约束**：两库是**同一文档的两个子表**，满足「只更改和维护这个文档，不新建链接」的要求。

**人工审核的价值压缩**：判定依据由系统自动生成并写入表格，人工不用再翻原始打分文件 —— 工作量从「翻文件 + 整理 + 录入」压缩为**只做判断**。人工只需改「审核状态」列 + 填备注。

---

## 4. 当前成果

### 4.1 可访问入口

| 产物 | 地址 |
| --- | --- |
| Demo 页面 | https://994ed259c73144b186f8400ac1ddf22c.app-tencent.workbuddy.link |
| 企微双库文档 | https://doc.weixin.qq.com/sheet/e3_AOYAMHheAGgCN6Zn0qLfPT2qgTh9D_a?scode=AJEAIQdfAAorU0WqNEAOYAMHheAGg |

文档名：`Badcase自动入库_Demo`，docid：`e3_AOYAMHheAGgCN6Zn0qLfPT2qgTh9D_a`

### 4.2 已验证的实测数据

Demo 数据范围：**全能帮写(128) + 高赞朋友圈(154) = 282 条用例**

| 输入 | 对齐用例 | Badcase | 严重度 | 覆盖分布 |
| --- | --- | --- | --- | --- |
| `打分结果_维度分列.csv` | 60 | 40 | P1 16 / P2 24 | LLM+机检 50、LLM+人工+机检 10 |
| `打分结果_数组式.xlsx` | 40 | 25 | P1 16 / P2 9 | LLM+机检 32、LLM+人工+机检 8 |

**真实写入记录**：大库 40 条 + 表头、小库 3 条（`--demo 3`），errcode 全 0。

> ⚠️ Demo 筛出率 66.7% 偏高，因刻意选取 B 侧低分样本以便展示效果。真实全量数据下约 **7%**（`engine.py` 跑 564 判次 → 40 条）。页面已标注。

### 4.3 前端 Demo 功能

- **六步流程进度条**：逐步点亮 + 实时执行日志
- **导入区**：本地文件（点击/拖拽）+ 企微链接双 Tab；内置两个真实样例可一键选用
- **解析结果卡片**：展示识别到的 ID 列、场景列、各打分方维度名、文本列、被忽略列
- **对齐判定 KPI**：对齐数、badcase 数、筛出率、严重度分布、覆盖分布、信号统计
- **双库对比面板**：大库/小库计数与定位说明
- **审核表格**：多维筛选（严重度/场景/状态）+ 详情抽屉（判定依据 + 三件套原文）+ 采纳用途下拉

---

## 5. 关键发现（务必阅读 —— 直接影响后续设计决策）

### 5.1 单一信号完全不可用

对两个场景的实测统计：

| 信号 | 命中数 | 说明 |
| --- | --- | --- |
| 硬伤（hard_fails） | **0** | 这两场景无硬性违规 |
| 机检不通过（pass=false） | **0** | 全部通过 |
| 人机分歧 | **0** | 人工 vs LLM 最大分差仅 **0.2** |
| 格式软违规（violations） | **299** | 极普遍 |

**结论**：LLM-Judge 已校准良好（这本身是好事，验证了小 Loop 校准 Kappa 的有效性），但意味着**靠"人机分歧"这类信号根本捞不到 badcase**。必须多路信号融合。

### 5.2 软违规必须降级为叠加信号

格式软违规有 299 条。若作为独立入库信号：

- 判定结果：**323 / 564 = 57%** 都成 badcase
- 后果：人工照样要看 323 条，**等于没筛选**

改为**叠加信号**（单独出现不入库，与其他信号共现时才计入）后：

- 筛出率回落到 **7.1%**（40/564）
- 同时帮 16 条从 P1/P2 升级为 P0（格式问题叠加低分 = 更该优先处理）

> 这个设计教训对后续加新信号同样适用：**先统计信号覆盖率，普遍性太高的信号必须降级或提高阈值**。

### 5.3 三路打分覆盖严重不均

| 数据源 | 覆盖 |
| --- | --- |
| 机检 D1 | 282 / 282（全量）|
| LLM-Judge | 60 |
| 人工锚定 | 10 |

工作流必须容忍「部分样本只有机检、部分有全部三路」。当前实现按「有哪路用哪路」判定（`primary = llm or human`），不因缺列失败。

### 5.4 三路数据可无损对齐

已实测三路数据通过 `case_id` 完全对齐，**零清洗成本**。这是整个方案最大的技术风险点，已排除。

---

## 6. 技术概设（设计方案）

### 6.1 分层架构

```
┌─────────────────────────────────────────────────────┐
│  前端 Demo（dist/index.html + data.js）               │
│  纯静态，无构建工具；数据由 Python 侧导出为 data.js     │
├─────────────────────────────────────────────────────┤
│  编排层（run_pipeline.py / accept_to_small.py）        │
│  六步流程串联，CLI 入口                                │
├──────────────────────┬──────────────────────────────┤
│  解析层 importer.py   │  业务层 pipeline.py            │
│  · 多格式读取         │  · align 对齐                  │
│  · 列语义识别         │  · judge 多信号判定             │
│  · 标准化抽取         │  · 双库写入 + 子表自动创建       │
├──────────────────────┴──────────────────────────────┤
│  企业微信在线表格（wecom-cli 子进程调用）               │
└─────────────────────────────────────────────────────┘
```

**设计原则**：
- `importer.py` 与 `pipeline.py` 均为**纯函数**（除写库外无副作用），便于单测与后续包装成 LangGraph 节点
- 不引第三方依赖（xlsx 自解 zip、Kappa 手写），降低部署摩擦
- 企微交互统一走 `pipeline.run()` 封装 `wecom-cli` 子进程

### 6.2 数据结构契约

**schema（解析产物）**
```python
{
  "id_col": int|None, "scene_col": int|None,
  "prev_col": int|None, "req_col": int|None, "cur_cols": [int],
  "score_groups": [
    {"side": "人工|LLM|机检|未标注",
     "cols": [int], "kind": "array|cols",
     "dims": [str], "header": str}
  ],
  "unknown": [int],
}
```

**record（标准化记录）**
```python
{
  "row": int, "case_id": str, "scene": str,
  "prev_output": str, "user_req": str, "cur_output": str,
  "scores": { "LLM": {"dims": [...], "values": [8.0, ...], "avg": 8.0}, ... },
}
```

**badcase（判定产物）**
```python
{
  "case_id", "scene", "signals": [str], "severity": "P0|P1|P2",
  "coverage": "LLM+机检", "action": str,
  "human_avg", "llm_avg", "mach_avg",
  "evidence": [str],           # 自动生成的判定依据，人工审核直接可读
  "prev_output", "user_req", "cur_output",
  "review_status": "待审核",
}
```

### 6.3 可配置项

`pipeline.TH` —— 判定阈值，前端未来可做成可调面板：
```python
TH = {"low_dim": 4, "low_overall": 6.0, "disagree": 1.5, "outlier_gap": 2.0}
```

`pipeline.DOCID` / `BIG_TITLE` / `SMALL_TITLE` —— 目标文档与子表名

### 6.4 企微写入约束（踩坑后固化的规则）

| 约束 | 说明 |
| --- | --- |
| **只能写机器人自己创建的文档** | 用户手建文档写入报 `640008`。必须由 `wecom-cli sheet create` 建表 |
| **子表 行×列 ≤ 10000** | 500×26=13000 报 `640027`。当前用 350×20 |
| **CLI stdout 有前缀** | 会先打印 `[wecom] json repair:` 等。解析须从**第一个**顶层 `{` 截取，用 `rfind` 会截到嵌套对象中间 |
| **新建表后有时序延迟** | 立即 `sheet get` 可能拿不到 `sheets`，需重试 |
| **分批写入** | 当前 10 行/批，避免 payload 过大与频控 |
| 链接类型路由 | `/sheet/` → `wecomcli-sheet` 技能；`/smartsheet/` → `wecomcli-smartsheet` |

---

## 7. 代码与文件说明

### 7.1 目录结构

```
badcase_pipeline/
├── README.md                    # 使用说明
├── DELIVERY.md                  # 本文档
├── importer.py         (269 行) # 通用解析器：多格式读取 + 列语义识别
├── pipeline.py         (233 行) # 对齐 / 判定 / 双库写入
├── run_pipeline.py      (66 行) # 六步流程编排入口
├── accept_to_small.py   (46 行) # 人工采纳 → 小库
├── make_samples.py     (119 行) # 生成两种形态测试样例
├── engine.py           (202 行) # 旧版固定路径引擎（保留参考，含额外 3 类信号）
├── samples/                     # 测试样例
│   ├── 打分结果_维度分列.csv
│   └── 打分结果_数组式.xlsx
└── dist/                        # 前端（部署产物）
    ├── index.html      (481 行) # 单页 Demo，无构建依赖
    ├── data.js                  # 由 Python 导出的真实结果
    ├── pipeline_result.json     # 流程完整输出
    └── 打分结果_*.csv/xlsx       # 可下载样例
```

### 7.2 模块接口

**importer.py**
```python
read_table(path)                        -> (headers, rows)   # CSV/XLSX
read_wecom_sheet(docid, sheet_id=None)  -> (headers, rows)   # 企微表格
parse_docid(link)                       -> docid|None
detect_schema(headers, rows)            -> schema
extract(headers, rows, schema)          -> [record]
summarize_schema(headers, schema, recs) -> dict              # 供前端展示
```

**pipeline.py**
```python
run(args)                    -> dict          # wecom-cli 封装（含 JSON 前缀处理）
ensure_sheets()              -> {title: sheet_id}  # 确保双库子表存在
align(records)               -> (aligned, coverage)
judge(aligned, th=None)      -> [badcase]
big_rows(recs)               -> [row]         # 大库 16 列
small_rows(recs, when)       -> [row]         # 小库 10 列
push(sheet_id, rows, start_row=0, batch=10) -> bool
```

**engine.py**（旧版，含未迁移的 3 类信号）
```python
load_sources(scenes=None)  -> (cases, mach, llm, human)  # 直读 eval_harness_v2
detect(cases, mach, llm, human, th=None) -> [record]     # 7 类信号
dedupe(records, existing_keys=None)      -> (new, upd)
summarize(cases, records)                -> dict
```

### 7.3 运行方式

```bash
cd badcase_pipeline
PY=/Users/huagnqinlin/.workbuddy/binaries/python/versions/3.13.12/bin/python3

# 只跑解析 + 判定（不写库）
$PY run_pipeline.py samples/打分结果_维度分列.csv

# 含真实写入大库
$PY run_pipeline.py samples/打分结果_维度分列.csv --write

# 企微表格链接作为输入
$PY run_pipeline.py "https://doc.weixin.qq.com/sheet/xxx"

# 人工采纳 → 小库
$PY accept_to_small.py 全能帮写#101 全能帮写#102
$PY accept_to_small.py --demo 3

# 重新生成测试样例
$PY make_samples.py

# 本地预览前端
cd dist && $PY -m http.server 8901
```

**前置条件**：`wecom-cli >= 1.1.0` 已安装且已授权（`wecom-cli auth show --status` 返回 `authorized`）。

---

## 8. 功能迭代历史

| # | 阶段 | 内容 | 产物 |
| --- | --- | --- | --- |
| 1 | 架构图 v1 | 单层 Loop mermaid 流程图 | `outputs/mermaid流程图.html` |
| 2 | 架构图 v2 | 四阶段 + 嵌套双层 Loop | `outputs/生图评测_双层Loop架构图.html` |
| 3 | 架构图 v3 | 改为**串行双 Loop 递进**（先小样本校准、后全量） | `outputs/生图评测_双层递进Loop架构图.html` |
| 4 | 架构图 v4 | 新增独立 **Badcase 沉淀·筛选·验证** 子图 | 同上（覆盖更新）|
| 5 | 架构图 v5 | 美化；踩坑：mermaid **subgraph 标题不支持 `\n`** 导致 Syntax error | 同上 |
| 6 | 架构图 v6 | 弃用 mermaid，改**纯 HTML/CSS 卡片信息图**（渲染稳定） | `outputs/生图评测_双层递进Loop架构图_卡片版.html` |
| 7 | 代码骨架 | 落地 **LangGraph 可运行项目**（3 Skill + 内层 Loop + 双层 Loop + Badcase 子图） | `image_eval_workflow/` |
| 8 | 可行性评估 | Badcase 入库环节评估：LangGraph vs Skill 形态对比、7 人日工期、风险矩阵 | `outputs/Badcase自动入库_可行性与工期评估.md` |
| 9 | Demo v1 | 自动监听式：三路直读 jsonl → 7 类信号 → 真实写入企微（新建表）| `badcase_pipeline/engine.py` |
| 10 | **Demo v2（当前）** | **降级自动监听 → 导入式**；通用解析器；双库（同文档双子表）；六步流程前端 | `badcase_pipeline/` 全量 |

### 8.1 v1 → v2 的具体变更

| 项 | v1 | v2 |
| --- | --- | --- |
| 触发方式 | 自动监听文件变更 | 用户主动导入 |
| 输入 | 硬编码读 `eval_harness_v2/*.jsonl` | CSV / XLSX / 企微链接，**列名任意** |
| 解析 | 无（路径固定） | 启发式列语义识别 + 解析结果展示 |
| 入库 | 单表，每次新建文档 | **双库**，固定维护同一文档 |
| 审核 | 表格内改状态列 | 前端交互 + 采纳用途选择 → 小库 |
| 信号数 | 7 类 | 4 类（3 类待从 engine.py 迁移）|

---

## 9. 技术栈

| 层 | 技术 | 说明 |
| --- | --- | --- |
| 运行时 | Python 3.13.12 | 路径 `/Users/huagnqinlin/.workbuddy/binaries/python/versions/3.13.12/bin/python3` |
| 依赖 | **零第三方依赖** | 仅标准库：`csv`/`json`/`re`/`zipfile`/`xml.etree`/`subprocess`/`collections` |
| XLSX 解析 | 自实现 | `zipfile` + `sharedStrings.xml` + `ElementTree`，避免引 openpyxl |
| 前端 | 原生 HTML/CSS/JS | **无框架、无构建**；单文件 481 行 + data.js |
| 企微集成 | `wecom-cli` 1.2.0 | 子进程调用，走 `sheet create/get/ranges get/contents update/subsheets add` |
| 部署 | CloudStudio 静态托管 | 沙箱 `994ed259c73144b186f8400ac1ddf22c` |
| 上游 LangGraph 骨架 | `langgraph >= 0.2` | 位于 `image_eval_workflow/`，独立 venv |

**为什么零依赖**：Demo 需要快速交付且环境不确定；xlsx 与 Kappa 都可手写实现，避免 pip 安装失败阻塞。若后续要支持复杂 xlsx（多 sheet、公式、合并单元格），建议引入 `openpyxl`。

---

## 10. 后续开发建议（按优先级）

### P0 · 让审核真正闭环

**问题**：当前前端点「采纳」只更新本地界面状态，不会回写企微小库。CLI 侧 `accept_to_small.py` 能写，但两者未打通。

**方案**：加一个轻量后端（FastAPI / Flask）暴露两个接口：
- `POST /api/import` — 接收上传文件或链接，跑 §3.1 的 ①–⑤ 步，返回 badcases
- `POST /api/accept` — 接收 `case_id` 列表 + 采纳用途，调 `pipeline.small_rows` + `push` 写小库

同时把大库「审核状态」列回写为「已采纳/已驳回」，实现状态双向同步。

### P1 · 补齐判定信号

把 `engine.py` 里已实现但未迁移的 3 类信号搬进 `pipeline.judge`：
- **硬伤**（`hard_fails` 非空）→ 直接 P0
- **AB 显著劣势**（同 case B 比 A 低 ≥3 分）→ 需要输入含 A/B 双侧数据
- **格式软违规**（`violations`）→ **务必保持叠加信号语义**（见 §5.2）

注意：这三类依赖机检的 `hard_fails`/`violations` 字段，通用解析器目前只识别数值型机检分。需扩展 schema 支持「机检明细文本列」。

### P1 · 去重与增量更新

`engine.dedupe()` 已实现逻辑但未接入 v2 主链路。需要：
1. 写库前先读大库现有 `case_id` 集合
2. 已存在 → 走 `contents update` 更新该行；不存在 → append
3. 避免重复导入同一份文件造成大库膨胀

### P2 · 落地为 LangGraph 节点

`importer` / `pipeline` 都是纯函数，包装成节点很直接：
- 各函数 → 节点；`badcases` 用 reducer 增量累积
- 加 `checkpointer`（MemorySaver / SqliteSaver）支持断点续跑
- 人工审核用「表格状态列轮询」替代 `interrupt`，零前端成本
- 参考已有骨架 `image_eval_workflow/image_eval/graph.py` 的装配方式

### P2 · 阈值可调面板

`pipeline.TH` 前端化，让评测同学自己调阈值并实时看筛出率变化 —— 这对信号调优很有价值（见 §5.2 的教训）。

### P3 · 多文件合并导入

当前一次只处理一个文件。真实场景可能需要同时导入「人工标注表 + LLM 打分表 + 机检结果表」三个独立文件，按 `case_id` join 后再判定。`pipeline.align` 已支持多方 `scores` 合并，扩展 `run_pipeline` 接受多路径即可。

---

## 11. 已知限制

| 限制 | 影响 | 缓解 |
| --- | --- | --- |
| 前端审核不回写企微 | 采纳状态仅存内存，刷新丢失 | 见 §10 P0 |
| 机器人无法写用户创建的文档 | 无法直接用业务同学手建的表 | 由机器人建表后加协作者，或走 Webhook |
| XLSX 解析仅支持 sheet1 + inlineStr/sharedStrings | 复杂 xlsx 可能读不全 | 需要时引入 openpyxl |
| 未接入去重 | 重复导入会重复写入 | 见 §10 P1 |
| Demo 筛出率偏高（66.7%） | 对外演示易被误解 | 页面已标注真实约 7% |
| 信号阈值未经充分调优 | 可能有误判/漏判 | 需用更多场景数据迭代 1–2 轮 |

---

## 12. 相关文件索引

| 用途 | 路径 |
| --- | --- |
| 本模块代码 | `badcase_pipeline/` |
| 本模块说明 | `badcase_pipeline/README.md` |
| 架构图（卡片版） | `outputs/生图评测_双层递进Loop架构图_卡片版.html` |
| LangGraph 骨架 | `image_eval_workflow/`（含独立 README） |
| 可行性与工期评估 | `outputs/Badcase自动入库_可行性与工期评估.md` |
| 原始评测数据 | `eval_harness_v2/`（all_cases / judge_a,b / d1_full_a,b / anchors）|
| 评测方法与量表 | `outputs/<场景>二轮_评测维度与打分量表.md`（8 场景）|
| 各场景 Prompt | `prompt/<场景>{首轮,2轮及之后轮次}-prompt.txt` |
| 项目长期记忆 | `.workbuddy/memory/MEMORY.md` |
| 开发日志 | `.workbuddy/memory/2026-09-05.md` 等 |
