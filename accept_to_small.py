"""人工审核采纳 → 写入小库（精选badcase库）。

用法：
    python accept_to_small.py case_id1 case_id2 ...
    python accept_to_small.py --demo 3    # 取前 N 条 P1 演示
"""
import json
import sys
import time

import pipeline as P


def main():
    data = json.load(open("dist/pipeline_result.json", encoding="utf-8"))
    bad = data["badcases"]

    if "--demo" in sys.argv:
        n = int(sys.argv[sys.argv.index("--demo") + 1])
        picked = [b for b in bad if b["severity"] == "P1"][:n]
    else:
        ids = [a for a in sys.argv[1:] if not a.startswith("--")]
        picked = [b for b in bad if b["case_id"] in ids]

    if not picked:
        print("no records picked")
        return 1

    sheets = P.ensure_sheets()
    small = sheets.get(P.SMALL_TITLE)
    if not small:
        print("small sheet missing")
        return 1

    when = time.strftime("%Y-%m-%d %H:%M")
    rows = P.small_rows(picked, when)
    print("采纳", len(picked), "条 → 写入小库")
    ok = P.push(small, rows)
    print("小库写入:", "成功" if ok else "失败")
    for b in picked:
        print("  +", b["case_id"], b["severity"], "、".join(b["signals"]))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
