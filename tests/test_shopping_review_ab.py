import unittest
from pathlib import Path
from unittest.mock import patch

from app.domain.merge import PersistedSourceFile
from app.infrastructure.shopping_review_ab import (
    A_LABEL,
    A_STAGE,
    B_LABEL,
    B_STAGE,
    DIMENSIONS,
    PROFILE,
    SHEET_NAME,
    ShoppingReviewPairedAdapter,
)


class ShoppingReviewPairedAdapterTests(unittest.TestCase):
    def test_splits_confirmed_report_columns_into_a_and_b_records(self) -> None:
        adapter = ShoppingReviewPairedAdapter()
        headers = adapter._required_headers()
        row = [""] * len(headers)
        index = {header: position for position, header in enumerate(headers)}
        row[index["序号"]] = "42"
        row[index["输入"]] = "用户希望生成购物神评"
        row[index["差值 (A−B)"]] = "1.2"
        row[index["胜出方"]] = "A"
        row[index["总评理由"]] = "优化后更符合要求"
        row[index["失败原因"]] = ""
        for label, values in ((A_LABEL, [8, 7, 6, 9, 10]), (B_LABEL, [6, 6, 7, 8, 9])):
            row[index[f"{label} 回答"]] = f"{label} 的回答"
            row[index[f"{label} 加权总分"]] = "7.8"
            row[index[f"{label} 各维度理由（合并）"]] = "维度理由"
            for dimension, value in zip(DIMENSIONS, values, strict=True):
                row[index[f"{dimension}·{label}分"]] = str(value)

        source = PersistedSourceFile(
            id="source-1",
            batch_id="batch-1",
            side="PAIR",
            evaluation_version="购物神评-首轮-v3-106",
            storage_path="imports/batch-1/report.xlsx",
            original_name="report.xlsx",
            schema={"profile": PROFILE},
        )
        with patch(
            "app.infrastructure.shopping_review_ab.legacy_importer.read_xlsx_sheet",
            return_value=(headers, [row]),
        ):
            aligned, issues = adapter.build_aligned_cases(source, Path("/safe/report.xlsx"))

        self.assertEqual(issues, [])
        self.assertEqual(len(aligned), 2)
        a, b = aligned
        self.assertEqual((a.case_id, a.side), ("42", "A"))
        self.assertEqual((b.case_id, b.side), ("42", "B"))
        self.assertEqual(a.payload["source_label"], A_LABEL)
        self.assertEqual(a.payload["prompt_stage"], A_STAGE)
        self.assertEqual(b.payload["source_label"], B_LABEL)
        self.assertEqual(b.payload["prompt_stage"], B_STAGE)
        self.assertEqual(a.payload["scores"]["LLM"]["values"], [8.0, 7.0, 6.0, 9.0, 10.0])
        self.assertEqual(a.payload["ab_evidence"]["winner"], "A")
        self.assertEqual(a.payload["ab_evidence"]["difference_a_minus_b"], "1.2")
        self.assertEqual(a.payload["user_req"], "用户希望生成购物神评")
        self.assertEqual(SHEET_NAME, "详细配对结果")


if __name__ == "__main__":
    unittest.main()
