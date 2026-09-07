"""通用打分文件解析器：自动识别评分维度与打分信息。

支持来源：
  - CSV / XLSX 本地文件
  - 企业微信在线表格链接（走 wecom-cli 读 CSV）

设计要点：不预设列名，靠启发式规则识别：
  - ID 列：含 case/id/编号/用例
  - 场景列：含 场景/scene/类型
  - 维度分列：列名形如 D1/D2… 或 含"分"且值域在 1-10，或 [x,x,x,x,x] 数组字符串
  - 文本列：上一轮/用户要求/本轮输出（含关键字）
  - 打分方 side：列名含 人工/human、LLM/judge/模型、机检/machine/规则
"""
import csv
import io
import json
import os
import re
import subprocess

DIM_PAT = re.compile(r"^(D\d|d\d)")
NUM_PAT = re.compile(r"^-?\d+(\.\d+)?$")
ARR_PAT = re.compile(r"^\s*[\[\(]\s*\d+(\s*[,，]\s*\d+)+\s*[\]\)]\s*$")

ID_KEYS = ["case_id", "caseid", "case", "id", "编号", "用例", "序号"]
SCENE_KEYS = ["场景", "scene", "类型", "category"]
PREV_KEYS = ["上一轮", "prev", "原输出", "上轮"]
REQ_KEYS = ["要求", "req", "指令", "需求", "user_req"]
CUR_KEYS = ["本轮", "输出", "output", "cur", "结果"]

HUMAN_KEYS = ["人工", "human", "专家", "标注"]
LLM_KEYS = ["llm", "judge", "模型", "机器打分", "ai"]
MACH_KEYS = ["机检", "machine", "规则", "校验", "d1检"]


def _norm(s):
    return str(s or "").strip().lower().replace(" ", "")


def _hit(name, keys):
    n = _norm(name)
    return any(k in n for k in keys)


def read_table(path, sheet_name=None):
    """读本地 CSV / XLSX，返回 (headers, rows)。XLSX 可选择工作表。"""
    ext = os.path.splitext(path)[1].lower()
    if ext in (".csv", ".txt", ".tsv"):
        if sheet_name is not None:
            raise ValueError("sheet_name is only supported for XLSX files")
        with open(path, encoding="utf-8-sig", newline="") as fh:
            delim = "\t" if ext == ".tsv" else ","
            rd = list(csv.reader(fh, delimiter=delim))
        return rd[0], rd[1:]
    if ext in (".xlsx", ".xlsm"):
        return _read_xlsx(path, sheet_name)
    raise ValueError("unsupported file type: " + ext)


def read_xlsx_sheet(path, sheet_name):
    """读取指定 XLSX 工作表，供受控来源适配器使用。"""
    return _read_xlsx(path, sheet_name)


def _read_xlsx(path, sheet_name=None):
    """极简 XLSX 读取（无第三方依赖，支持按工作表名称选择）。"""
    import zipfile
    from xml.etree import ElementTree as ET

    NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    with zipfile.ZipFile(path) as z:
        shared = []
        if "xl/sharedStrings.xml" in z.namelist():
            root = ET.fromstring(z.read("xl/sharedStrings.xml"))
            for si in root.findall(NS + "si"):
                shared.append("".join(t.text or "" for t in si.iter(NS + "t")))
        name = _xlsx_sheet_path(z, ET, sheet_name)
        root = ET.fromstring(z.read(name))
        grid = []
        for row in root.iter(NS + "row"):
            cells = {}
            for c in row.findall(NS + "c"):
                ref = c.get("r") or ""
                col = re.sub(r"\d", "", ref)
                t = c.get("t")
                v = c.find(NS + "v")
                isn = c.find(NS + "is")
                if t == "s" and v is not None:
                    val = shared[int(v.text)] if v.text and int(v.text) < len(shared) else ""
                elif t == "inlineStr" and isn is not None:
                    val = "".join(x.text or "" for x in isn.iter(NS + "t"))
                else:
                    val = v.text if v is not None else ""
                cells[col] = val or ""
            grid.append(cells)
    if not grid:
        return [], []
    cols = sorted({c for r in grid for c in r}, key=lambda s: (len(s), s))
    table = [[r.get(c, "") for c in cols] for r in grid]
    return table[0], table[1:]


def _xlsx_sheet_path(archive, ET, sheet_name):
    main_ns = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    rel_ns = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
    package_rel_ns = "{http://schemas.openxmlformats.org/package/2006/relationships}"
    workbook = ET.fromstring(archive.read("xl/workbook.xml"))
    sheets = workbook.findall(main_ns + "sheets/" + main_ns + "sheet")
    if not sheets:
        raise ValueError("xlsx contains no worksheets")
    selected = sheets[0] if sheet_name is None else next(
        (sheet for sheet in sheets if sheet.get("name") == sheet_name),
        None,
    )
    if selected is None:
        raise ValueError("xlsx worksheet not found: " + str(sheet_name))
    relation_id = selected.get(rel_ns + "id")
    rels = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
    target = next(
        (
            rel.get("Target")
            for rel in rels.findall(package_rel_ns + "Relationship")
            if rel.get("Id") == relation_id
        ),
        None,
    )
    if not target:
        raise ValueError("xlsx worksheet relationship is missing")
    return "xl/" + target.lstrip("/")


def read_wecom_sheet(docid, sheet_id=None):
    """读企微在线表格，返回 (headers, rows)。"""
    def run(args):
        p = subprocess.run(args, capture_output=True, text=True)
        out = p.stdout.strip()
        i = out.find(chr(123) + chr(10))
        if i == -1:
            i = out.find(chr(123))
        if i > 0:
            out = out[i:]
        return json.loads(out)

    if not sheet_id:
        got = run(["wecom-cli", "sheet", "get", "--json",
        json.dumps({"docid": docid}, ensure_ascii=False)])
        sheet_id = got["sheets"][0]["sheet_id"]
            
        got = run(["wecom-cli", "sheet", "ranges", "get", "--json",
        json.dumps({"docid": docid, "sheet_id": sheet_id, "mode": "csv"},
      ensure_ascii=False)])
    content = got.get("content")
    if not content and got.get("file_path"):
        content = open(got["file_path"], encoding="utf-8-sig").read()
    rd = list(csv.reader(io.StringIO(content or "")))
    return (rd[0], rd[1:]) if rd else ([], [])


def parse_docid(link):
    """从企微文档链接提取 docid。"""
    m = re.search(r"/(?:sheet|smartsheet|doc)/([A-Za-z0-9_\-]+)", link or "")
    return m.group(1) if m else None


def _col_values(rows, idx):
    return [r[idx] for r in rows if idx < len(r) and str(r[idx]).strip() != ""]


def detect_schema(headers, rows):
    """自动识别列语义，返回 schema 描述。"""
    schema = {
        "id_col": None, "scene_col": None,
        "prev_col": None, "req_col": None, "cur_cols": [],
      "score_groups": [],   # [{"side":..,"cols":[i..],"dims":[名..],"kind":"array"|"cols"}]
        "unknown": [],
    }

    for i, h in enumerate(headers):
        if schema["id_col"] is None and _hit(h, ID_KEYS):
            schema["id_col"] = i
            continue
        if schema["scene_col"] is None and _hit(h, SCENE_KEYS):
            schema["scene_col"] = i
            continue
        if schema["prev_col"] is None and _hit(h, PREV_KEYS):
            schema["prev_col"] = i
            continue
        if schema["req_col"] is None and _hit(h, REQ_KEYS):
            schema["req_col"] = i
            continue

    # 打分列：数组型
    for i, h in enumerate(headers):
        vals = _col_values(rows, i)
        if not vals:
            continue
        arr_like = sum(1 for v in vals[:20] if ARR_PAT.match(str(v)))
        if arr_like >= max(1, min(len(vals), 20) // 2):
            side = ("人工" if _hit(h, HUMAN_KEYS) else
                   "机检" if _hit(h, MACH_KEYS) else
                   "LLM" if _hit(h, LLM_KEYS) else "未标注")
            n = len(re.findall(r"\d+", str(vals[0])))
            schema["score_groups"].append({
                "side": side, "cols": [i], "kind": "array",
                "dims": ["D" + str(k + 1) for k in range(n)],
                "header": h,
            })
            
    # 打分列：单列数值型（列名带 D1/D2 或 含"分"）
    used = {c for g in schema["score_groups"] for c in g["cols"]}
    numeric = []
    for i, h in enumerate(headers):
        if i in used or i in (schema["id_col"], schema["scene_col"],
                              schema["prev_col"], schema["req_col"]):
            continue
        vals = _col_values(rows, i)
        if not vals:
            continue
        num_like = sum(1 for v in vals[:20] if NUM_PAT.match(str(v).strip()))
        if num_like < max(1, min(len(vals), 20) * 0.8):
            continue
        in_range = sum(1 for v in vals[:20]
                    if NUM_PAT.match(str(v).strip()) and 0 <= float(v) <= 10)
        if DIM_PAT.match(str(h).strip()) or "分" in str(h) or in_range >= num_like * 0.8:
            numeric.append((i, h))
            
    # 按 side 归组
    groups = {}
    for i, h in numeric:
        side = ("人工" if _hit(h, HUMAN_KEYS) else
               "机检" if _hit(h, MACH_KEYS) else
               "LLM" if _hit(h, LLM_KEYS) else "未标注")
        groups.setdefault(side, []).append((i, h))
    for side, items in groups.items():
        schema["score_groups"].append({
            "side": side, "cols": [i for i, _ in items], "kind": "cols",
            "dims": [h for _, h in items], "header": "、".join(h for _, h in items),
            })

    # 输出文本列
    used = {c for g in schema["score_groups"] for c in g["cols"]}
    for i, h in enumerate(headers):
        if i in used or i in (schema["id_col"], schema["scene_col"],
                              schema["prev_col"], schema["req_col"]):
            continue
        if _hit(h, CUR_KEYS):
            schema["cur_cols"].append(i)
        else:
            schema["unknown"].append(i)

    return schema


def extract(headers, rows, schema):
    """按 schema 抽取标准化记录。"""
    out = []
    for ri, r in enumerate(rows):
        def cell(i):
            return r[i] if i is not None and i < len(r) else ""

        rec = {
            "row": ri + 2,
            "case_id": cell(schema["id_col"]) or ("row#" + str(ri + 2)),
            "scene": cell(schema["scene_col"]) or "未分类",
            "prev_output": cell(schema["prev_col"]),
            "user_req": cell(schema["req_col"]),
            "cur_output": " / ".join(cell(i) for i in schema["cur_cols"] if cell(i)),
        "scores": {},
        }
        for g in schema["score_groups"]:
            if g["kind"] == "array":
                raw = cell(g["cols"][0])
                nums = [float(x) for x in re.findall(r"\d+(?:\.\d+)?", str(raw))]
            else:
                nums = []
                for i in g["cols"]:
                    v = str(cell(i)).strip()
                    nums.append(float(v) if NUM_PAT.match(v) else None)
            vals = [n for n in nums if n is not None]
            if vals:
                rec["scores"][g["side"]] = {
                    "dims": g["dims"], "values": nums,
                    "avg": round(sum(vals) / len(vals), 2),
                    }
        if rec["scores"]:
            out.append(rec)
    return out


def summarize_schema(headers, schema, records):
    sides = [g["side"] for g in schema["score_groups"]]
    return {
        "columns": len(headers),
     "rows_parsed": len(records),
        "id_col": headers[schema["id_col"]] if schema["id_col"] is not None else None,
      "scene_col": headers[schema["scene_col"]] if schema["scene_col"] is not None else None,
        "score_sides": sides,
        "dims_by_side": {g["side"]: g["dims"] for g in schema["score_groups"]},
        "text_cols": {
     "prev": headers[schema["prev_col"]] if schema["prev_col"] is not None else None,
      "req": headers[schema["req_col"]] if schema["req_col"] is not None else None,
     "cur": [headers[i] for i in schema["cur_cols"]],
        },
      "ignored_cols": [headers[i] for i in schema["unknown"]],
    }
