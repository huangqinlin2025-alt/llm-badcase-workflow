"""六步流程编排：导入 → 解析 → 对齐 → 判定 → 入大库 → 人工审核(入小库)。

大库/小库为同一企微文档下的两个子表，只维护、不新建。
"""
import json
import os
import re
import subprocess

import importer as I

# 固定维护的目标文档（不再新建）
DOCID = "e3_AOYAMHheAGgCN6Zn0qLfPT2qgTh9D_a"
URL = ("https://doc.weixin.qq.com/sheet/e3_AOYAMHheAGgCN6Zn0qLfPT2qgTh9D_a"
       "?scode=AJEAIQdfAAorU0WqNEAOYAMHheAGg")
BIG_TITLE = "badcase大库(全量筛出)"
SMALL_TITLE = "精选badcase库(小库)"

TH = {
    "low_dim": 4,
    "low_overall": 6.0,
    "disagree": 1.5,
    "outlier_gap": 2.0,
}

BIG_HEADERS = [
    "case_id", "场景", "触发信号", "严重度", "打分覆盖", "建议动作",
    "人工均分", "LLM均分", "机检分", "判定依据",
    "上一轮输出", "用户要求", "本轮输出", "审核状态", "审核人", "审核备注",
]
SMALL_HEADERS = [
    "case_id", "场景", "严重度", "触发信号", "归因", "采纳用途",
    "上一轮输出", "用户要求", "本轮输出", "采纳时间",
]


def run(args):
    p = subprocess.run(args, capture_output=True, text=True)
    out = p.stdout.strip()
    i = out.find(chr(123) + chr(10))
    if i == -1:
        i = out.find(chr(123))
    if i > 0:
        out = out[i:]
    try:
        return json.loads(out)
    except Exception:
        return {"errcode": -1, "raw": (out or p.stderr)[:300]}


def ensure_sheets():
    """确保大库/小库两个子表存在，返回 {title: sheet_id}。"""
    got = run(["wecom-cli", "sheet", "get", "--json",
              json.dumps({"docid": DOCID}, ensure_ascii=False)])
    have = {s["title"]: s["sheet_id"] for s in got.get("sheets", [])}
    for title in (BIG_TITLE, SMALL_TITLE):
        if title not in have:
            r = run(["wecom-cli", "sheet", "subsheets", "add", "--json",
                         json.dumps({"docid": DOCID,
                         "sheet": {"title": title, "row_count": 350,
                         "column_count": 20},
                         "index": 0}, ensure_ascii=False)])
            if r.get("errcode") == 0:
                have[title] = r["sheet"]["sheet_id"]
    return have


# ---------- Step 3: 对齐 ----------
def align(records):
    """按 case_id 归并同一用例的多方打分，输出对齐结果与覆盖统计。"""
    by_id = {}
    for r in records:
        k = r["case_id"]
        if k not in by_id:
            by_id[k] = r
        else:
            by_id[k]["scores"].update(r["scores"])
    aligned = list(by_id.values())
    cov = {}
    for r in aligned:
        key = "+".join(sorted(r["scores"].keys())) or "无"
        cov[key] = cov.get(key, 0) + 1
    return aligned, cov


# ---------- Step 4: 判定 ----------
def judge(aligned):
    """多信号判定 badcase。维度均值按场景动态计算。"""
    means = {}
    for r in aligned:
        for side, s in r["scores"].items():
            for i, v in enumerate(s["values"]):
                if v is None:
                    continue
                means.setdefault((r["scene"], side, i), []).append(v)
    means = {k: sum(v) / len(v) for k, v in means.items()}

    out = []
    for r in aligned:
        sig, ev = [], []
        sc = r["scores"]
        human = sc.get("人工")
        llm = sc.get("LLM")
        mach = sc.get("机检")
        primary = llm or human
        if not primary:
            continue

        low = [primary["dims"][i] for i, v in enumerate(primary["values"])
               if v is not None and v <= TH["low_dim"]]
        if low:
            sig.append("绝对低分")
            ev.append("低分维度：" + "、".join(low))
        elif primary["avg"] < TH["low_overall"]:
            sig.append("绝对低分")
            ev.append("综合均分 " + str(primary["avg"]) + " 低于 " + str(TH["low_overall"]))

        if human and llm:
            gap = abs(human["avg"] - llm["avg"])
            if gap >= TH["disagree"]:
                sig.append("人机分歧")
                ev.append("人工 " + str(human["avg"]) + " vs LLM " + str(llm["avg"])
                          + "，差 " + str(round(gap, 2)))

        if mach and llm and mach["avg"] <= 6 and llm["values"][0] and llm["values"][0] >= 8:
            sig.append("机检LLM冲突")
            ev.append("机检 " + str(mach["avg"]) + " 偏低但 LLM D1 给 "
                      + str(llm["values"][0]))

        side_name = "LLM" if llm else "人工"
        for i, v in enumerate(primary["values"]):
            if v is None:
                continue
            m = means.get((r["scene"], side_name, i))
            if m and m - v >= TH["outlier_gap"]:
                sig.append("维度离群")
                ev.append(primary["dims"][i] + " " + str(v)
                          + " 低于场景均值 " + str(round(m, 2)))
                break

        if not sig:
            continue

        severity = "P0" if len(sig) >= 3 else ("P1" if len(sig) == 2
                                              or "人机分歧" in sig else "P2")
        if "人机分歧" in sig or "机检LLM冲突" in sig:
            action = "修正Judge量表"
        elif "绝对低分" in sig:
            action = "候选Golden"
        else:
            action = "补Few-shot"

        out.append({
            "case_id": r["case_id"],
            "scene": r["scene"],
            "signals": sig,
            "severity": severity,
            "coverage": "+".join(sorted(sc.keys())),
            "action": action,
            "human_avg": human["avg"] if human else None,
            "llm_avg": llm["avg"] if llm else None,
            "mach_avg": mach["avg"] if mach else None,
            "evidence": ev,
            "prev_output": r["prev_output"],
            "user_req": r["user_req"],
            "cur_output": r["cur_output"],
            "review_status": "待审核",
        })
    return out


# ---------- Step 5/6: 写库 ----------
def cell(v):
    return {"cell_value": {"text": str(v)}, "data_type": "TEXT", "cell_format": {}}


def numcell(v):
    if v is None:
        return cell("-")
    return {"cell_value": {"number": float(v)}, "data_type": "NUMBER", "cell_format": {}}


def clip(s, n=400):
    s = str(s or "").replace("\r", "")
    return s if len(s) <= n else s[:n] + "…"


def big_rows(recs):
    rows = [{"values": [cell(h) for h in BIG_HEADERS]}]
    for r in recs:
        rows.append({"values": [
            cell(r["case_id"]), cell(r["scene"]),
            cell("、".join(r["signals"])), cell(r["severity"]),
            cell(r["coverage"]), cell(r["action"]),
            numcell(r["human_avg"]), numcell(r["llm_avg"]), numcell(r["mach_avg"]),
            cell(clip("；".join(r["evidence"]), 300)),
            cell(clip(r["prev_output"])), cell(clip(r["user_req"], 200)),
            cell(clip(r["cur_output"])),
            cell(r["review_status"]), cell(""), cell(""),
        ]})
    return rows


def small_rows(recs, when):
    rows = [{"values": [cell(h) for h in SMALL_HEADERS]}]
    for r in recs:
        rows.append({"values": [
            cell(r["case_id"]), cell(r["scene"]), cell(r["severity"]),
            cell("、".join(r["signals"])),
            cell(clip("；".join(r["evidence"]), 200)),
            cell(r.get("accept_use", r["action"])),
            cell(clip(r["prev_output"])), cell(clip(r["user_req"], 200)),
            cell(clip(r["cur_output"])), cell(when),
        ]})
    return rows


def push(sheet_id, rows, start_row=0, batch=10):
    cursor = 0
    while cursor < len(rows):
        chunk = rows[cursor:cursor + batch]
        payload = {"docid": DOCID, "sheet_id": sheet_id,
                   "grid_data": {"start_row": start_row + cursor,
                                 "start_column": 0, "rows": chunk}}
        r = run(["wecom-cli", "sheet", "contents", "update", "--json",
                 json.dumps(payload, ensure_ascii=False)])
        code = r.get("errcode", -1)
        print("   rows", start_row + cursor, "-",
              start_row + cursor + len(chunk) - 1, "errcode=", code)
        if code != 0:
            return False
        cursor += len(chunk)
    return True
