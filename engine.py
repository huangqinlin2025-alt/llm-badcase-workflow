"""Badcase 自动入库管线 · 数据加载与对齐（三路打分）。"""
import json
import glob
import os
from collections import defaultdict, Counter

SRC = "/Users/huagnqinlin/生图冷启动prompt评测/eval_harness_v2"
DIMS = ["D1格式合规", "D2要求遵循", "D3一致性", "D4差异化", "D5自然度"]
DEMO_SCENES = ["全能帮写", "高赞朋友圈"]

DEFAULT_THRESHOLDS = {
    "low_dim": 4,
    "low_overall": 6.0,
    "disagree": 1.5,
    "conflict_llm_d1": 8,
    "ab_gap": 3.0,
    "outlier_gap": 2.0,
}


def _load_jsonl(path):
    with open(path, encoding="utf-8") as fh:
        rows = [json.loads(l) for l in fh if l.strip()]
    return rows


def _avg(xs):
    return round(sum(xs) / len(xs), 2) if xs else None


def load_sources(scenes=None):
    """加载三路打分 + 原文三件套，按 case_id 对齐。"""
    scenes = scenes or DEMO_SCENES
    cases = {}
    for c in _load_jsonl(SRC + "/all_cases.jsonl"):
        if c["scene"] in scenes:
            cases[c["case_id"]] = c
    mach = {"A": {}, "B": {}}
    for side, fn in (("A", "d1_full_a.jsonl"), ("B", "d1_full_b.jsonl")):
        for r in _load_jsonl(SRC + "/" + fn):
            if r["scene"] in scenes:
                mach[side][r["case_id"]] = r
    llm = {"A": {}, "B": {}}
    for side, pat in (("A", "judge_a_*.jsonl"), ("B", "judge_b_*.jsonl")):
        for path in glob.glob(SRC + "/" + pat):
            scene = os.path.basename(path).split("_", 2)[-1].replace(".jsonl", "")
            if scene not in scenes:
                continue
            for r in _load_jsonl(path):
                llm[side][r["id"]] = r["s"]
    human = {"A": {}, "B": {}}
    for scene in scenes:
        path = SRC + "/anchors_" + scene + ".jsonl"
        if not os.path.exists(path):
            continue
        for r in _load_jsonl(path):
            human["A"][r["id"]] = {"s": r["A_scores(D1-D5)"], "note": r.get("A_note", "")}
            human["B"][r["id"]] = {"s": r["B_scores(D1-D5)"], "note": r.get("B_note", "")}
    return cases, mach, llm, human


def _scene_dim_means(cases, llm):
    """按场景计算各维度均值，用于维度离群检测。"""
    acc = defaultdict(lambda: defaultdict(list))
    for cid, c in cases.items():
        for side in ("A", "B"):
            s = llm[side].get(cid)
            if s:
                for i, v in enumerate(s):
                    acc[c["scene"]][i].append(v)
    return {sc: {i: _avg(v) for i, v in d.items()} for sc, d in acc.items()}


def detect(cases, mach, llm, human, th=None):
    """6 类信号判定 + 归因 + 建议动作。"""
    th = {**DEFAULT_THRESHOLDS, **(th or {})}
    dim_means = _scene_dim_means(cases, llm)
    records = []
    for cid in sorted(cases):
        c = cases[cid]
        scene = c["scene"]
        for side in ("A", "B"):
            m = mach[side].get(cid)
            l = llm[side].get(cid)
            h = human[side].get(cid)
            signals, evidence = [], []
            coverage = ("人" if h else "") + ("L" if l else "") + ("机" if m else "")
            coverage = coverage or "无"
            l_avg = _avg(l) if l else None
            h_avg = _avg(h["s"]) if h else None
            if l:
                low_dims = [DIMS[i] for i, v in enumerate(l) if v <= th["low_dim"]]
                if low_dims:
                    signals.append("绝对低分")
                    evidence.append("低分维度：" + "、".join(low_dims))
                elif l_avg is not None and l_avg < th["low_overall"]:
                    signals.append("绝对低分")
                    evidence.append("综合均分 " + str(l_avg) + " 低于 " + str(th["low_overall"]))
            if m and m.get("hard_fails"):
                signals.append("硬伤")
                evidence.append("硬性违规：" + "; ".join(m["hard_fails"]))
            if m and m.get("violations"):
                signals.append("格式软违规")
                evidence.append("格式违规：" + "; ".join(m["violations"]))
            if l_avg is not None and h_avg is not None:
                gap = abs(h_avg - l_avg)
                if gap >= th["disagree"]:
                    signals.append("人机分歧")
                    evidence.append("人工 " + str(h_avg) + " vs LLM " + str(l_avg) + "，差 " + str(round(gap, 2)))
            if m and l and not m.get("pass", True) and l[0] >= th["conflict_llm_d1"]:
                signals.append("机检LLM冲突")
                evidence.append("机检D1判不通过，LLM D1 却给 " + str(l[0]))
            if side == "B":
                la, lb = llm["A"].get(cid), llm["B"].get(cid)
                if la and lb:
                    ga, gb = _avg(la), _avg(lb)
                    if ga - gb >= th["ab_gap"]:
                        signals.append("AB显著劣势")
                        evidence.append("A " + str(ga) + " vs B " + str(gb) + "，落后 " + str(round(ga - gb, 2)))
            if l and scene in dim_means:
                for i, v in enumerate(l):
                    mean = dim_means[scene].get(i)
                    if mean and mean - v >= th["outlier_gap"]:
                        signals.append("维度离群")
                        evidence.append(DIMS[i] + " " + str(v) + " 低于场景均值 " + str(mean))
                        break
            if not signals:
                continue
            # 格式软违规过于普遍(299/564)，单独出现不入库，仅作为叠加信号
            if signals == ["格式软违规"]:
                continue
            if "硬伤" in signals or len(signals) >= 3:
                severity = "P0"
            elif len(signals) == 2 or "人机分歧" in signals:
                severity = "P1"
            else:
                severity = "P2"
            if "人机分歧" in signals or "机检LLM冲突" in signals:
                action = "修正Judge量表"
            elif "硬伤" in signals or "格式软违规" in signals:
                action = "Prompt格式约束"
            elif "AB显著劣势" in signals:
                action = "补Few-shot"
            else:
                action = "候选Golden"
            records.append({
                "case_id": cid,
                "scene": scene,
                "side": side,
                "signals": signals,
                "severity": severity,
                "coverage": coverage,
                "human_scores": h["s"] if h else None,
                "human_note": h.get("note", "") if h else "",
                "llm_scores": l,
                "llm_avg": l_avg,
                "human_avg": h_avg,
                "mach_d1": m.get("d1_score") if m else None,
                "mach_pass": m.get("pass") if m else None,
                "violations": m.get("violations", []) if m else [],
                "hard_fails": m.get("hard_fails", []) if m else [],
                "evidence": evidence,
                "action": action,
                "prev_output": c["prev_output"],
                "user_req": c["user_req"],
                "cur_output": c["output_a"] if side == "A" else c["output_b"],
                "review_status": "待审核",
            })
    return records


def dedupe(records, existing_keys=None):
    """跨轮次去重：同 case_id+side 已入库则归为更新，否则新增。"""
    existing_keys = existing_keys or set()
    new, upd = [], []
    for r in records:
        key = r["case_id"] + "::" + r["side"]
        if key in existing_keys:
            upd.append(r)
        else:
            new.append(r)
    return new, upd


def summarize(cases, records):
    """统计摘要，供前端展示。"""
    return {
        "total_cases": len(cases),
        "total_judged": len(cases) * 2,
        "badcase_count": len(records),
        "by_signal": dict(Counter(s for r in records for s in r["signals"])),
        "by_severity": dict(Counter(r["severity"] for r in records)),
        "by_action": dict(Counter(r["action"] for r in records)),
        "by_scene": dict(Counter(r["scene"] for r in records)),
        "by_coverage": dict(Counter(r["coverage"] for r in records)),
    }


if __name__ == "__main__":
    cases, mach, llm, human = load_sources()
    recs = detect(cases, mach, llm, human)
    print(json.dumps(summarize(cases, recs), ensure_ascii=False, indent=2))
