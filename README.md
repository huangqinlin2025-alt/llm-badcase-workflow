# Badcase 入库工作流 · 导入解析 → 审核入库

从架构图「Badcase 沉淀 · 筛选 · 验证」环节落地。**用户主动导入打分文件**（不再自动监听），
自动识别评分维度 → 对齐判定 → 先入大库 → 人工采纳后入精选小库。

## 在线体验

- **Demo 页面（历史托管）**：https://994ed259c73144b186f8400ac1ddf22c.app-tencent.workbuddy.link
- **GitHub Pages（自动发布）**：https://huangqinlin2025-alt.github.io/llm-badcase-workflow/（首次推送并在仓库设置中启用 GitHub Pages 后生效）
- **双库企微文档**（固定维护同一份，不新建）：
  [badcase库](https://doc.weixin.qq.com/sheet/e3_AOYAMHheAGgCN6Zn0qLfPT2qgTh9D_a?scode=AJEAIQdfAAorU0WqNEAOYAMHheAGg)
  - 子表「badcase大库(全量筛出)」——判定命中即自动写入
  - 子表「精选badcase库(小库)」——人工采纳后才写入

## 六步流程

| 步骤 | 内容 | 自动/人工 |
| --- | --- | --- |
| ① 导入 | CSV / XLSX / 企微表格链接 | 人工触发 |
| ② 解析 | 自动识别 ID、场景、三方打分维度、三件套文本 | 自动 |
| ③ 对齐 | 按 `case_id` 归并多方打分，统计覆盖分布 | 自动 |
| ④ 判定 | 多信号识别 badcase + 归因 + 严重度 + 建议动作 | 自动 |
| ⑤ 入大库 | 全量筛出结果写入企微子表 | 自动 |
| ⑥ 人工审核 | 采纳 → 写入精选小库；驳回 → 仅留大库 | 人工 |

## 自动解析能力（核心）

**不依赖固定列名**，靠启发式规则识别。已用两种差异极大的样例验证：

| 样例 | 列名风格 | 识别结果 |
| --- | --- | --- |
| `打分结果_维度分列.csv` | 「用例编号/业务场景/专家-格式分/模型打分-D1/规则校验得分」 | ✅ 三方打分全部正确归类，忽略「备注说明」 |
| `打分结果_数组式.xlsx` | 「case/场景分类/人工评分(D1-D5)」，值为 `[8,8,9,7,8]` | ✅ 数组式打分正确拆解为 5 维 |

识别规则：
- **ID 列**：含 case/id/编号/用例/序号
- **场景列**：含 场景/scene/类型
- **打分方**：列名含 人工/专家/human → 人工；llm/judge/模型 → LLM；机检/规则/校验 → 机检
- **维度**：列名形如 `D1` 或含「分」且值域 0-10；或整列为 `[x,x,x]` 数组字符串
- **文本列**：含 上一轮/prev、要求/req、本轮/输出

## 判定信号

绝对低分（任一维度≤4 或综合<6）· 人机分歧（|人工−LLM|≥1.5）· 机检LLM冲突 · 维度离群（低于场景均值≥2）

严重度：≥3 信号 → P0；2 信号或含人机分歧 → P1；否则 P2

## 双库设计

| | 大库 | 小库 |
| --- | --- | --- |
| 写入时机 | 判定命中即自动写入 | 人工审核采纳后 |
| 定位 | 问题全景 + 追溯底账 | 反哺模型/量表的高价值集合 |
| 列数 | 16 列（含判定依据、审核状态） | 10 列（含采纳用途、采纳时间） |

两库为**同一文档的两个子表**，满足「只更改和维护这个文档，不新建链接」。

## 使用

```bash
# 只跑解析+判定（不写库）
python run_pipeline.py samples/打分结果_维度分列.csv

# 含真实写入大库
python run_pipeline.py samples/打分结果_维度分列.csv --write

# 企微表格链接作为输入
python run_pipeline.py "https://doc.weixin.qq.com/sheet/xxx"

# 人工采纳 → 写入小库
python accept_to_small.py 全能帮写#101 全能帮写#102
python accept_to_small.py --demo 3
```

## API 主体框架（开发中）

ARC-001 已建立 FastAPI 服务骨架与健康检查；导入、SQLite、企微同步和审核 API 将在后续规格任务中接入。

```bash
PY=/Users/huagnqinlin/.workbuddy/binaries/python/versions/3.13.12/bin/python3
$PY -m pip install -e '.[dev]'
$PY -m uvicorn app.main:app --host 127.0.0.1 --port 8000
# 浏览器或 curl 访问：http://127.0.0.1:8000/healthz
```

当前已支持真实本地文件导入。每个文件都必须提供显式 `side` 和 `evaluation_version`；文件会被解析并生成持久化报告，但尚未执行多文件合并、badcase 判定或企微写入。

```bash
curl -X POST http://127.0.0.1:8000/api/v1/import-batches \
  -H 'Idempotency-Key: local-import-0001' \
  -F 'project_id=default' \
  -F 'sides=B' \
  -F 'evaluation_versions=eval-v1' \
  -F 'files=@samples/打分结果_维度分列.csv'

# 使用返回的 import_id 精确合并同批次多份来源文件
curl -X POST http://127.0.0.1:8000/api/v1/import-batches/<import_id>/merge \
  -H 'Idempotency-Key: local-merge-0001'
```

合并键固定为 `case_id + side + evaluation_version`。人工、LLM、机检分数可分别来自不同文件；同一评分方分数或非空文本冲突会生成 `MERGE_CONFLICT`，并使对应 case 不进入对齐结果。

已支持受控的“购物神评成对 AB 报告”适配器。上传时使用 `side=PAIR` 和 `source_profiles=shopping-review-paired-ab-v1`，并在同一请求中显式给出 `evaluation_version`。适配器固定读取 `详细配对结果`，将 `HY-Vision-2.0-instruct` 映射为 A/优化后 Prompt、`quinta_gouwushenping_firstround` 映射为 B/优化前 Prompt；其他工作簿不可自动复用该映射。

请将 `.env.example` 复制为本地 `.env` 后再填入后续企微配置；不要提交真实令牌、文档 ID、SQLite 数据库或用户数据。

## 文件说明

| 文件 | 作用 |
| --- | --- |
| `importer.py` | 通用解析器：CSV/XLSX/企微表格读取 + 自动列语义识别 |
| `pipeline.py` | 对齐、判定、双库写入（含子表自动创建） |
| `run_pipeline.py` | 六步流程编排入口 |
| `accept_to_small.py` | 人工采纳 → 小库 |
| `make_samples.py` | 生成两种形态的测试样例 |
| `engine.py` | 旧版固定路径引擎（保留参考） |
| `dist/` | 前端 Demo + 数据 + 可下载样例 |

## 踩坑记录

1. **`640008 不允许操作非机器人创建的文件`** —— 机器人只能写自己创建的文档。
   用户手建的表格无法写入，需由机器人 `sheet create` 建表。
2. **`640027 rowCount*columnCount 超出范围`** —— 子表行×列上限 **10000**。
   500×26=13000 会被拒，改用 350×20。
3. **CLI stdout 前缀干扰 JSON 解析** —— `wecom-cli` 会先打印 `[wecom] json repair:` 等提示。
   须从**第一个**顶层 `{` 截取；用 `rfind` 会截到嵌套对象中间导致 KeyError。
4. **演示样本取样偏差** —— 初版取 `judge_a` 前 60 条恰好全是高分（均分 8.0），判定出 0 条 badcase。
   低分样本集中在 `judge_b`，改用 B 侧数据后恢复正常。
