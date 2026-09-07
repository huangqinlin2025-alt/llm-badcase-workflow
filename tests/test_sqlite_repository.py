import sqlite3
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from app.domain.errors import ConcurrencyConflict, DuplicateRequest
from app.domain.models import NewCandidate, NewImportBatch, NewOutboxTask, NewReviewDecision
from app.domain.status import ImportBatchStatus, ReviewStatus
from app.infrastructure.sqlite_repository import SqliteWorkflowRepository


class SqliteWorkflowRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = TemporaryDirectory()
        self.database_path = Path(self.temp_dir.name) / "runtime" / "badcase.db"
        self.repository = SqliteWorkflowRepository(self.database_path)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _create_batch(self, batch_id: str = "batch-1") -> None:
        self.repository.create_import_batch_with_outbox(
            NewImportBatch(
                id=batch_id,
                project_id="project-1",
                idempotency_key=f"idempotency-{batch_id}",
                request_hash=f"request-{batch_id}",
                created_by="operator-1",
                status=ImportBatchStatus.RECEIVED,
            ),
            NewOutboxTask(
                id=f"task-{batch_id}",
                batch_id=batch_id,
                operation="PROJECT_BIG_LIBRARY",
                payload={"batch_id": batch_id},
            ),
        )

    def _candidate(self, content_hash: str = "content-v1") -> NewCandidate:
        return NewCandidate(
            id="candidate-1",
            batch_id="batch-1",
            candidate_key="case-1::B::eval-v1::core-v1",
            content_hash=content_hash,
            rule_set_version="core-v1",
            scene="全能帮写",
            severity="P1",
            evidence={"signals": ["绝对低分"]},
        )

    def test_migrations_are_idempotent_and_create_fact_tables(self) -> None:
        SqliteWorkflowRepository(self.database_path)
        connection = sqlite3.connect(self.database_path)
        try:
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                ).fetchall()
            }
            migrations = connection.execute("SELECT version FROM schema_migrations").fetchall()
        finally:
            connection.close()

        self.assertTrue(
            {
                "import_batches",
                "source_files",
                "parse_issues",
                "aligned_cases",
                "candidates",
                "review_decisions",
                "sheet_projections",
                "outbox_tasks",
                "audit_events",
            }.issubset(tables)
        )
        self.assertEqual(migrations, [("001_initial.sql",)])

    def test_batch_and_outbox_are_saved_atomically_and_idempotency_is_enforced(self) -> None:
        self._create_batch()
        self.assertEqual(self.repository.get_import_batch_status("batch-1"), "RECEIVED")
        tasks = self.repository.list_pending_outbox()
        self.assertEqual([(task.id, task.operation) for task in tasks], [("task-batch-1", "PROJECT_BIG_LIBRARY")])

        with self.assertRaises(DuplicateRequest):
            self.repository.create_import_batch_with_outbox(
                NewImportBatch(
                    id="batch-duplicate",
                    project_id="project-1",
                    idempotency_key="idempotency-batch-1",
                    request_hash="request-batch-duplicate",
                    created_by="operator-1",
                ),
                NewOutboxTask(
                    id="task-duplicate",
                    batch_id="batch-duplicate",
                    operation="PROJECT_BIG_LIBRARY",
                    payload={"batch_id": "batch-duplicate"},
                ),
            )

        self.assertIsNone(self.repository.get_import_batch_status("batch-duplicate"))
        self.assertEqual(len(self.repository.list_pending_outbox()), 1)

    def test_pending_candidate_can_update_but_terminal_review_is_preserved(self) -> None:
        self._create_batch()
        initial = self.repository.upsert_candidate(self._candidate())
        changed = self.repository.upsert_candidate(self._candidate("content-v2"))
        self.assertEqual(changed.version, initial.version + 1)
        self.assertEqual(changed.content_hash, "content-v2")

        approved = self.repository.review_candidate(
            NewReviewDecision(
                id="review-1",
                candidate_id=changed.id,
                action=ReviewStatus.APPROVED,
                actor_id="reviewer-1",
                expected_version=changed.version,
                use="入 Golden 金标准",
                note="approved for regression set",
            )
        )
        replay = self.repository.upsert_candidate(self._candidate("content-v3"))

        self.assertEqual(approved.review_status, ReviewStatus.APPROVED)
        self.assertEqual(replay.content_hash, "content-v2")
        self.assertEqual(replay.version, approved.version)
        self.assertEqual(replay.review_status, ReviewStatus.APPROVED)

    def test_review_uses_optimistic_locking(self) -> None:
        self._create_batch()
        candidate = self.repository.upsert_candidate(self._candidate())
        approved = self.repository.review_candidate(
            NewReviewDecision(
                id="review-1",
                candidate_id=candidate.id,
                action=ReviewStatus.APPROVED,
                actor_id="reviewer-1",
                expected_version=candidate.version,
            )
        )
        self.assertEqual(approved.version, candidate.version + 1)

        with self.assertRaises(ConcurrencyConflict):
            self.repository.review_candidate(
                NewReviewDecision(
                    id="review-2",
                    candidate_id=candidate.id,
                    action=ReviewStatus.REJECTED,
                    actor_id="reviewer-2",
                    expected_version=candidate.version,
                )
            )


if __name__ == "__main__":
    unittest.main()
