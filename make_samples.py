"""生成测试用打分文件（CSV + XLSX），列名故意与项目内不同，验证自动识别能力。"""
import csv
import io
import json
import os
import zipfile

SRC = "/Users/huagnqinlin/生图冷启动prompt评测/eval_harness_v2"
OUT = "/Users/huagnqinlin/生图冷启动prompt评测/badcase_pipeline/samples"
os.makedirs(OUT, exist_ok=True)


def load(fn):
    return [json.loads(l) for l in open(SRC + "/" + fn, encoding="utf-8") if l.strip()]


cases = {c["case_id"]: c for c in load("all_cases.jsonl")}
d1 = {r["case_id"]: r for r in load("d1_full_a.jsonl")}
judge = {}
for sc in ["全能帮写", "高赞朋友圈"]:
    for r in load("judge_b_" + sc + ".jsonl"):
        judge[r["id"]] = r["s"]
anchors = {}
for sc in ["全能帮写", "高赞朋友圈"]:
    for r in load("anchors_" + sc + ".jsonl"):
        anchors[r["id"]] = r["B_scores(D1-D5)"]

# --- 形态1：CSV，维度拆成独立列，列名口语化 ---
h1 = ["用例编号", "业务场景", "上一轮内容", "本轮用户指令", "本轮生成结果",
      "专家-格式分", "专家-遵循分", "专家-一致分", "专家-差异分", "专家-自然分",
      "模型打分-D1", "模型打分-D2", "模型打分-D3", "模型打分-D4", "模型打分-D5",
      "规则校验得分", "备注说明"]
rows1 = []
for cid in list(judge)[:60]:
    c = cases.get(cid)
    if not c:
        continue
    j = judge[cid]
    a = anchors.get(cid)
    rows1.append([
        cid, c["scene"], c["prev_output"], c["user_req"], c["output_b"],
        *(a if a else ["", "", "", "", ""]),
        *j,
        (d1.get(cid) or {}).get("d1_score", ""),
        (d1.get(cid) or {}).get("violations", [""])[0] if (d1.get(cid) or {}).get("violations") else "",
    ])
with open(OUT + "/打分结果_维度分列.csv", "w", encoding="utf-8-sig", newline="") as fh:
    w = csv.writer(fh)
    w.writerow(h1)
    w.writerows(rows1)
print("CSV rows:", len(rows1))

# --- 形态2：XLSX，打分是数组字符串 ---
h2 = ["case", "场景分类", "上轮输出", "用户要求", "本轮输出",
      "人工评分(D1-D5)", "LLM评分(D1-D5)", "机检D1"]
rows2 = []
for cid in list(judge)[:40]:
    c = cases.get(cid)
    if not c:
        continue
    a = anchors.get(cid)
    rows2.append([
        cid, c["scene"], c["prev_output"], c["user_req"], c["output_b"],
        str(a) if a else "", str(judge[cid]),
        str((d1.get(cid) or {}).get("d1_score", "")),
    ])


def col_name(n):
    s = ""
    while n >= 0:
        s = chr(65 + n % 26) + s
        n = n // 26 - 1
    return s


def esc(v):
    return (str(v).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


sheet_rows = []
allr = [h2] + rows2
for ri, r in enumerate(allr, 1):
    cs = []
    for ci, v in enumerate(r):
        ref = col_name(ci) + str(ri)
        cs.append('<c r="' + ref + '" t="inlineStr"><is><t>' + esc(v) + "</t></is></c>")
    sheet_rows.append('<row r="' + str(ri) + '">' + "".join(cs) + "</row>")

NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
sheet_xml = ('<?xml version="1.0" encoding="UTF-8"?><worksheet xmlns="' + NS
             + '"><sheetData>' + "".join(sheet_rows) + "</sheetData></worksheet>")
wb = ('<?xml version="1.0" encoding="UTF-8"?><workbook xmlns="' + NS
      + '" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
      '<sheets><sheet name="Sheet1" sheetId="1" r:id="rId1"/></sheets></workbook>')
wb_rels = ('<?xml version="1.0" encoding="UTF-8"?><Relationships '
           'xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
           '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/'
           'officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
           "</Relationships>")
ct = ('<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/'
      'package/2006/content-types"><Default Extension="rels" ContentType="application/'
      'vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" '
      'ContentType="application/xml"/><Override PartName="/xl/workbook.xml" '
      'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
      '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/'
      'vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>')
root_rels = ('<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://'
             'schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" '
             'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/'
             'officeDocument" Target="xl/workbook.xml"/></Relationships>')

with zipfile.ZipFile(OUT + "/打分结果_数组式.xlsx", "w", zipfile.ZIP_DEFLATED) as z:
    z.writestr("[Content_Types].xml", ct)
    z.writestr("_rels/.rels", root_rels)
    z.writestr("xl/workbook.xml", wb)
    z.writestr("xl/_rels/workbook.xml.rels", wb_rels)
    z.writestr("xl/worksheets/sheet1.xml", sheet_xml)
print("XLSX rows:", len(rows2))
