"""Controlled adapter for the confirmed 购物神评 paired AB XLSX report."""
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

import importer as legacy_importer

from app.domain.imports import ImportValidationError
from app.domain.merge import NewAlignedCase, PersistedSourceFile
from app.domain.models import NewParseIssue

PROFILE = "shopping-review-paired-ab-v1"
SHEET_NAME = "详细配对结果"
SCENE = "购物神评"
A_LABEL = "HY-Vision-2.0-instruct"
B_LABEL = "quinta_gouwushenping_firstround"
A_STAGE = "优化后 Prompt"
B_STAGE = "优化前 Prompt"
DIMENSIONS = ["输入理解与采信逻辑", "内容质量", "差异化与角色分工", "输出格式合规", "安全红线"]


@dataclass(frozen=True)
class PairedReportTable:
    headers: list[str]
    rows: list[list[str]]
    indices: dict[str, int]


class ShoppingReviewPairedAdapter:
    def read_table(self, path: Path) -> PairedReportTable:
        headers, rows = legacy_importer.read_xlsx_sheet(str(path), SHEET_NAME)
        indices = {header: index for index, header in enumerate(headers)}
        required = self._required_headers()
        missing = [header for header in required if header not in indices]
        if missing:
            raise ImportValidationError("shopping review paired report is missing columns: " + ", ".join(missing))
        return PairedReportTable(headers=headers, rows=rows, indices=indices)

    def summarize(self, table: PairedReportTable) -> dict[str, object]:
        return {
            "profile": PROFILE,
            "worksheet": SHEET_NAME,
            "columns": len(table.headers),
            "rows_parsed": sum(1 for row in table.rows if self._pair_id(row, table.indices)),
            "id_col": "序号",
            "scene_col": None,
            "score_sides": ["LLM"],
            "dims_by_side": {"LLM": DIMENSIONS},
            "text_cols": {"prev": None, "req": "输入", "cur": [f"{A_LABEL} 回答", f"{B_LABEL} 回答"]},
            "ignored_cols": [],
            "ab_mapping": {
                "A": {"source_label": A_LABEL, "prompt_stage": A_STAGE},
                "B": {"source_label": B_LABEL, "prompt_stage": B_STAGE},
            },
        }

    def build_aligned_cases(
        self, source: PersistedSourceFile, path: Path
    ) -> tuple[list[NewAlignedCase], list[NewParseIssue]]:
        table = self.read_table(path)
        aligned: list[NewAlignedCase] = []
        issues: list[NewParseIssue] = []
        for row_number, row in enumerate(table.rows, start=2):
            pair_id = self._pair_id(row, table.indices)
            if not pair_id:
                issues.append(self._issue(source, row_number, "missing 配对序号"))
                continue
            payloads, error = self._pair_payloads(source, row, table.indices, pair_id, row_number)
            if error:
                issues.append(self._issue(source, row_number, error))
                continue
            for side, payload in payloads:
                aligned.append(
                    NewAlignedCase(
                        id=str(uuid4()),
                        batch_id=source.batch_id,
                        case_id=pair_id,
                        side=side,
                        evaluation_version=source.evaluation_version,
                        payload=payload,
                    )
                )
        return aligned, issues

    def _pair_payloads(
        self,
        source: PersistedSourceFile,
        row: list[str],
        indices: dict[str, int],
        pair_id: str,
        row_number: int,
    ) -> tuple[list[tuple[str, dict[str, object]]], str | None]:
        user_req = self._cell(row, indices, "输入")
        evidence = {
            "pair_row_number": row_number,
            "pair_id": pair_id,
            "weighted_total_a": self._cell(row, indices, f"{A_LABEL} 加权总分"),
            "weighted_total_b": self._cell(row, indices, f"{B_LABEL} 加权总分"),
            "difference_a_minus_b": self._cell(row, indices, "差值 (A−B)"),
            "winner": self._cell(row, indices, "胜出方"),
            "overall_reason": self._cell(row, indices, "总评理由"),
            "failure_reason": self._cell(row, indices, "失败原因"),
        }
        result: list[tuple[str, dict[str, object]]] = []
        for side, label, stage in (("A", A_LABEL, A_STAGE), ("B", B_LABEL, B_STAGE)):
            output = self._cell(row, indices, f"{label} 回答")
            values = [self._number(self._cell(row, indices, f"{dimension}·{label}分")) for dimension in DIMENSIONS]
            if not output or not any(value is not None for value in values):
                return [], f"pair {pair_id} is missing {side} output or scores"
            numeric = [value for value in values if value is not None]
            payload = {
                "case_id": pair_id,
                "side": side,
                "evaluation_version": source.evaluation_version,
                "scene": SCENE,
                "prev_output": "",
                "user_req": user_req,
                "cur_output": output,
                "scores": {
                    "LLM": {
                        "dims": DIMENSIONS,
                        "values": values,
                        "avg": round(sum(numeric) / len(numeric), 2),
                    }
                },
                "source_file_ids": [source.id],
                "source_label": label,
                "prompt_stage": stage,
                "ab_evidence": evidence,
            }
            result.append((side, payload))
        return result, None

    @staticmethod
    def _number(value: str) -> float | None:
        try:
            return float(value) if str(value).strip() else None
        except ValueError:
            return None

    @staticmethod
    def _cell(row: list[str], indices: dict[str, int], header: str) -> str:
        index = indices[header]
        return str(row[index] if index < len(row) else "")

    @staticmethod
    def _pair_id(row: list[str], indices: dict[str, int]) -> str:
        return str(row[indices["序号"]] if indices["序号"] < len(row) else "").strip()

    @staticmethod
    def _issue(source: PersistedSourceFile, row_number: int, detail: str) -> NewParseIssue:
        return NewParseIssue(
            id=str(uuid4()),
            batch_id=source.batch_id,
            file_id=source.id,
            row_no=row_number,
            code="MERGE_AB_INVALID_ROW",
            detail=detail,
        )

    @staticmethod
    def _required_headers() -> list[str]:
        headers = ["序号", "输入", "差值 (A−B)", "胜出方", "总评理由", "失败原因"]
        for label in (A_LABEL, B_LABEL):
            headers.extend((f"{label} 回答", f"{label} 加权总分", f"{label} 各维度理由（合并）"))
            headers.extend(f"{dimension}·{label}分" for dimension in DIMENSIONS)
        return headers
