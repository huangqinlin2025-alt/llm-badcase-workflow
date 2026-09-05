"""六步流程端到端测试（Step5/6 可选真实写库）。

用法：
    python run_pipeline.py samples/打分结果_维度分列.csv          # 只跑 1-4 步
    python run_pipeline.py samples/打分结果_维度分列.csv --write   # 含写入大库
"""
import json
import sys
import time

import importer as I
import pipeline as P


def main():
    src = sys.argv[1]
    do_write = "--write" in sys.argv

    print("STEP1 导入：", src)
    if src.startswith("http"):
        docid = I.parse_docid(src)
        headers, rows = I.read_wecom_sheet(docid)
    else:
        headers, rows = I.read_table(src)
    print("  原始行数:", len(rows), "列数:", len(headers))

    print("STEP2 解析：自动识别维度与打分方")
    schema = I.detect_schema(headers, rows)
    recs = I.extract(headers, rows, schema)
    summary = I.summarize_schema(headers, schema, recs)
    print("  " + json.dumps(summary, ensure_ascii=False))

    print("STEP3 对齐：")
    aligned, cov = P.align(recs)
    print("  对齐用例:", len(aligned), "覆盖分布:", cov)

    print("STEP4 判定：")
    bad = P.judge(aligned)
    from collections import Counter
    print("  badcase:", len(bad), "/", len(aligned),
          "严重度:", dict(Counter(b["severity"] for b in bad)),
          "信号:", dict(Counter(s for b in bad for s in b["signals"])))

    out = {"import": {"file": src, "rows": len(rows), "cols": len(headers)},
           "schema": summary, "coverage": cov,
           "aligned": len(aligned), "badcases": bad}
    with open("dist/pipeline_result.json", "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1)
    print("  -> dist/pipeline_result.json")

    if do_write:
        print("STEP5 入大库（写入企微文档，维护同一链接）：")
        sheets = P.ensure_sheets()
        print("  子表:", list(sheets.keys()))
        big = sheets.get(P.BIG_TITLE)
        ok = P.push(big, P.big_rows(bad))
        print("  大库写入:", "成功" if ok else "失败")
        print("  URL:", P.URL)
    else:
        print("STEP5 入大库：跳过（加 --write 执行真实写入）")
    print("STEP6 人工审核 → 采纳后入小库：由前端交互驱动")
    return 0


if __name__ == "__main__":
    sys.exit(main())
