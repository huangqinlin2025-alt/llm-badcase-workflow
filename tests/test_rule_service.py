import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from app.application.rule_service import RuleService
from app.domain.merge import NewAlignedCase
from app.domain.models import NewImportBatch
from app.domain.status import ImportBatchStatus
from app.infrastructure.sqlite_repository import SqliteWorkflowRepository


DIMS = ["输入理解与采信逻辑", "内容质量", "差异化与角色分工", "输出格式合规", "安全红线"]


def payload(side: str, values: list[float], stage: str) -> dict[str, object]:
    return {
        "case_id": "pair-1",
        "side": side,
        "evaluation_version": "eval-v1",
        "scene": "购物神评",
        "scores": {"LLM": {"dims": DIMS, "values": values, "avg": sum(values) / len(values)}},
        "prompt_stage": stage,
        "source_label": side,
        "metric_profile": "shopping-review-paired-ab-v1",
    }


class RuleServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = TemporaryDirectory()
        self.repository = SqliteWorkflowRepository(Path(self.temp_dir.name) / "badcase.db")
        batch = NewImportBatch(
            id="batch-1",
            project_id="project-1",
            idempotency_key="rule-service-key",
            request_hash="rule-service-request",
            created_by="tester",
            status=ImportBatchStatus.ALIGNED,
        )
        self.repository.create_import_batch_with_sources(batch=batch, source_files=[], issues=[])
        self.repository.replace_aligned_cases(
            batch_id="batch-1",
            aligned_cases=[
                NewAlignedCase("aligned-a", "batch-1", "pair-1", "A", "eval-v1", payload("A", [5.5, 7, 7, 9, 9], "优化后 Prompt")),
                NewAlignedCase("aligned-b", "batch-1", "pair-1", "B", "eval-v1", payload("B", [8, 7, 7, 9, 9], "优化前 Prompt")),
            ],
            issues=[],
            status=ImportBatchStatus.ALIGNED,
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_evaluate_persists_layered_candidate_and_updates_batch(self) -> None:
        report = RuleService(self.repository).evaluate_batch("batch-1")
        self.assertEqual(report["status"], "DECIDED")
        self.assertEqual(len(report["candidates"]), 1)
        candidate = report["candidates"][0]
        self.assertEqual(candidate["severity"], "P1")
        self.assertEqual(candidate["evidence"]["metric_level"], "L2")
        self.assertEqual(candidate["evidence"]["value_grade"], "PENDING_AGGREGATION")
        self.assertEqual(candidate["evidence"]["review_strategy"], "SAMPLE_50_PERCENT")


if __name__ == "__main__":
    unittest.main()
