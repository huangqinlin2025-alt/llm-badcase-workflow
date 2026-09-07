"""SQLite implementation of the workflow fact ledger."""
from contextlib import contextmanager
from datetime import UTC, datetime
import json
from pathlib import Path
import sqlite3
from typing import Iterator, Sequence

from app.domain.errors import ConcurrencyConflict, DuplicateRequest
from app.domain.merge import NewAlignedCase, PersistedSourceFile
from app.domain.models import (
    CandidateSnapshot,
    NewAuditEvent,
    NewCandidate,
    NewImportBatch,
    NewOutboxTask,
    NewParseIssue,
    NewReviewDecision,
    NewSourceFile,
    OutboxSnapshot,
)
from app.domain.status import ImportBatchStatus, OutboxState, ReviewStatus
from app.infrastructure.migrations import MigrationRunner


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class SqliteWorkflowRepository:
    """Persist business facts locally before any external projection is attempted."""

    def __init__(self, database_path: Path) -> None:
        self._database_path = database_path
        MigrationRunner(database_path).apply()

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self._database_path, timeout=5, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        connection.execute("BEGIN IMMEDIATE")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    @contextmanager
    def _read_connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self._database_path, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
        finally:
            connection.close()

    def create_import_batch_with_outbox(self, batch: NewImportBatch, task: NewOutboxTask) -> None:
        """Persist a batch and one external operation in the same transaction."""
        if task.batch_id != batch.id or task.candidate_id is not None:
            raise ValueError("initial import task must reference only its import batch")
        try:
            with self._transaction() as connection:
                now = _utc_now()
                self._insert_import_batch(connection, batch, now)
                self._insert_outbox(connection, task, now)
        except sqlite3.IntegrityError as error:
            self._raise_duplicate_request(error)

    def create_import_batch_with_sources(
        self,
        *,
        batch: NewImportBatch,
        source_files: Sequence[NewSourceFile],
        issues: Sequence[NewParseIssue],
    ) -> None:
        """Persist parsed input facts atomically; projection begins only after rules create candidates."""
        try:
            with self._transaction() as connection:
                now = _utc_now()
                self._insert_import_batch(connection, batch, now)
                for source in source_files:
                    if source.batch_id != batch.id or source.project_id != batch.project_id:
                        raise ValueError("source file must belong to the import batch and project")
                    connection.execute(
                        """
                        INSERT INTO source_files(
                            id, batch_id, project_id, sha256, source_type, side, evaluation_version,
                            original_name, storage_path, schema_json, rows_total, rows_parsed, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            source.id,
                            source.batch_id,
                            source.project_id,
                            source.sha256,
                            source.source_type,
                            source.side,
                            source.evaluation_version,
                            source.original_name,
                            source.storage_path,
                            _json(source.schema),
                            source.rows_total,
                            source.rows_parsed,
                            now,
                        ),
                    )
                for issue in issues:
                    if issue.batch_id != batch.id:
                        raise ValueError("parse issue must belong to the import batch")
                    connection.execute(
                        """
                        INSERT INTO parse_issues(id, batch_id, file_id, row_no, code, detail, created_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            issue.id,
                            issue.batch_id,
                            issue.file_id,
                            issue.row_no,
                            issue.code,
                            issue.detail,
                            now,
                        ),
                    )
        except sqlite3.IntegrityError as error:
            self._raise_duplicate_request(error)

    def list_source_files(self, batch_id: str) -> list[PersistedSourceFile]:
        with self._read_connection() as connection:
            rows = connection.execute(
                """
                SELECT id, batch_id, side, evaluation_version, storage_path, original_name, schema_json
                FROM source_files
                WHERE batch_id = ?
                ORDER BY created_at, id
                """,
                (batch_id,),
            ).fetchall()
        return [
            PersistedSourceFile(
                id=row["id"],
                batch_id=row["batch_id"],
                side=row["side"],
                evaluation_version=row["evaluation_version"],
                storage_path=row["storage_path"],
                original_name=row["original_name"],
                schema=json.loads(row["schema_json"]),
            )
            for row in rows
        ]

    def replace_aligned_cases(
        self,
        *,
        batch_id: str,
        aligned_cases: Sequence[NewAlignedCase],
        issues: Sequence[NewParseIssue],
        status: ImportBatchStatus,
    ) -> None:
        if status not in {ImportBatchStatus.ALIGNED, ImportBatchStatus.PARTIAL_FAILURE}:
            raise ValueError("merge status must be ALIGNED or PARTIAL_FAILURE")
        now = _utc_now()
        with self._transaction() as connection:
            if connection.execute(
                "SELECT 1 FROM import_batches WHERE id = ?", (batch_id,)
            ).fetchone() is None:
                raise LookupError("import batch not found")
            connection.execute("DELETE FROM aligned_cases WHERE batch_id = ?", (batch_id,))
            connection.execute(
                "DELETE FROM parse_issues WHERE batch_id = ? AND code LIKE 'MERGE_%'", (batch_id,)
            )
            for aligned in aligned_cases:
                if aligned.batch_id != batch_id:
                    raise ValueError("aligned case must belong to the import batch")
                connection.execute(
                    """
                    INSERT INTO aligned_cases(
                        id, batch_id, case_id, side, evaluation_version, payload_json, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        aligned.id,
                        aligned.batch_id,
                        aligned.case_id,
                        aligned.side,
                        aligned.evaluation_version,
                        _json(aligned.payload),
                        now,
                        now,
                    ),
                )
            for issue in issues:
                if issue.batch_id != batch_id or not issue.code.startswith("MERGE_"):
                    raise ValueError("merge issue must belong to the batch and use a MERGE_ code")
                connection.execute(
                    """
                    INSERT INTO parse_issues(id, batch_id, file_id, row_no, code, detail, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (issue.id, issue.batch_id, issue.file_id, issue.row_no, issue.code, issue.detail, now),
                )
            connection.execute(
                "UPDATE import_batches SET status = ?, updated_at = ? WHERE id = ?",
                (status.value, now, batch_id),
            )

    def get_import_batch_by_idempotency(
        self, project_id: str, idempotency_key: str
    ) -> dict[str, str] | None:
        with self._read_connection() as connection:
            row = connection.execute(
                """
                SELECT id, request_hash, status
                FROM import_batches
                WHERE project_id = ? AND idempotency_key = ?
                """,
                (project_id, idempotency_key),
            ).fetchone()
        return dict(row) if row else None

    def get_import_batch_status(self, batch_id: str) -> str | None:
        with self._read_connection() as connection:
            row = connection.execute(
                "SELECT status FROM import_batches WHERE id = ?", (batch_id,)
            ).fetchone()
        return row["status"] if row else None

    def set_import_batch_status(self, batch_id: str, status: str) -> None:
        with self._transaction() as connection:
            updated = connection.execute(
                "UPDATE import_batches SET status = ?, updated_at = ? WHERE id = ?",
                (status, _utc_now(), batch_id),
            )
            if updated.rowcount != 1:
                raise LookupError("import batch not found")

    def list_aligned_payloads(self, batch_id: str) -> list[dict[str, object]]:
        with self._read_connection() as connection:
            rows = connection.execute(
                """
                SELECT payload_json FROM aligned_cases
                WHERE batch_id = ? ORDER BY case_id, side, evaluation_version
                """,
                (batch_id,),
            ).fetchall()
        return [json.loads(row["payload_json"]) for row in rows]

    def delete_pending_candidates(self, batch_id: str, rule_set_version: str) -> None:
        with self._transaction() as connection:
            connection.execute(
                """
                DELETE FROM candidates
                WHERE batch_id = ? AND rule_set_version = ? AND review_status = ?
                """,
                (batch_id, rule_set_version, ReviewStatus.PENDING_REVIEW.value),
            )

    def get_import_batch_report(self, batch_id: str) -> dict[str, object]:
        with self._read_connection() as connection:
            batch = connection.execute(
                "SELECT id, project_id, status, created_by, created_at FROM import_batches WHERE id = ?",
                (batch_id,),
            ).fetchone()
            if batch is None:
                raise LookupError("import batch not found")
            files = connection.execute(
                """
                SELECT id, original_name, source_type, side, evaluation_version, sha256,
                       schema_json, rows_total, rows_parsed
                FROM source_files WHERE batch_id = ? ORDER BY created_at, id
                """,
                (batch_id,),
            ).fetchall()
            issues = connection.execute(
                """
                SELECT file_id, row_no, code, detail FROM parse_issues
                WHERE batch_id = ? ORDER BY row_no, id
                """,
                (batch_id,),
            ).fetchall()
            aligned = connection.execute(
                """
                SELECT case_id, side, evaluation_version, payload_json
                FROM aligned_cases WHERE batch_id = ? ORDER BY case_id, side, evaluation_version
                """,
                (batch_id,),
            ).fetchall()
            candidates = connection.execute(
                """
                SELECT id, candidate_key, rule_set_version, scene, severity, review_status, version, evidence_json
                FROM candidates WHERE batch_id = ? ORDER BY severity, candidate_key
                """,
                (batch_id,),
            ).fetchall()
        aligned_payloads = [json.loads(row["payload_json"]) for row in aligned]
        coverage: dict[str, int] = {}
        for payload in aligned_payloads:
            key = "+".join(sorted(payload.get("scores", {}).keys())) or "无"
            coverage[key] = coverage.get(key, 0) + 1
        return {
            "import_id": batch["id"],
            "project_id": batch["project_id"],
            "status": batch["status"],
            "created_by": batch["created_by"],
            "created_at": batch["created_at"],
            "sources": [
                {
                    "id": row["id"],
                    "filename": row["original_name"],
                    "source_type": row["source_type"],
                    "side": row["side"],
                    "evaluation_version": row["evaluation_version"],
                    "sha256": row["sha256"],
                    "schema": json.loads(row["schema_json"]),
                    "rows_total": row["rows_total"],
                    "rows_parsed": row["rows_parsed"],
                }
                for row in files
            ],
            "issues": [dict(row) for row in issues],
            "aligned": len(aligned_payloads),
            "coverage": coverage,
            "aligned_cases": aligned_payloads,
            "candidates": [
                {
                    "id": row["id"],
                    "candidate_key": row["candidate_key"],
                    "rule_set_version": row["rule_set_version"],
                    "scene": row["scene"],
                    "severity": row["severity"],
                    "review_status": row["review_status"],
                    "version": row["version"],
                    "evidence": json.loads(row["evidence_json"]),
                }
                for row in candidates
            ],
        }

    def upsert_candidate(self, candidate: NewCandidate) -> CandidateSnapshot:
        now = _utc_now()
        with self._transaction() as connection:
            existing = connection.execute(
                "SELECT * FROM candidates WHERE candidate_key = ?", (candidate.candidate_key,)
            ).fetchone()
            if existing is None:
                connection.execute(
                    """
                    INSERT INTO candidates(
                        id, batch_id, candidate_key, content_hash, rule_set_version,
                        scene, severity, evidence_json, review_status, version, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                    """,
                    (
                        candidate.id,
                        candidate.batch_id,
                        candidate.candidate_key,
                        candidate.content_hash,
                        candidate.rule_set_version,
                        candidate.scene,
                        candidate.severity,
                        _json(candidate.evidence),
                        ReviewStatus.PENDING_REVIEW.value,
                        now,
                        now,
                    ),
                )
            elif existing["review_status"] == ReviewStatus.PENDING_REVIEW.value:
                content_changed = existing["content_hash"] != candidate.content_hash
                connection.execute(
                    """
                    UPDATE candidates
                    SET content_hash = ?, rule_set_version = ?, scene = ?, severity = ?, evidence_json = ?,
                        version = version + ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        candidate.content_hash,
                        candidate.rule_set_version,
                        candidate.scene,
                        candidate.severity,
                        _json(candidate.evidence),
                        1 if content_changed else 0,
                        now,
                        existing["id"],
                    ),
                )
            row = connection.execute(
                "SELECT * FROM candidates WHERE candidate_key = ?", (candidate.candidate_key,)
            ).fetchone()
        return self._candidate_snapshot(row)

    def review_candidate(self, decision: NewReviewDecision) -> CandidateSnapshot:
        if decision.action not in {ReviewStatus.APPROVED, ReviewStatus.REJECTED}:
            raise ValueError("review action must be APPROVED or REJECTED")

        now = _utc_now()
        with self._transaction() as connection:
            updated = connection.execute(
                """
                UPDATE candidates
                SET review_status = ?, version = version + 1, updated_at = ?
                WHERE id = ? AND version = ? AND review_status = ?
                """,
                (
                    decision.action.value,
                    now,
                    decision.candidate_id,
                    decision.expected_version,
                    ReviewStatus.PENDING_REVIEW.value,
                ),
            )
            if updated.rowcount != 1:
                raise ConcurrencyConflict("candidate was already reviewed or changed")
            connection.execute(
                """
                INSERT INTO review_decisions(
                    id, candidate_id, action, use_text, note, actor_id, expected_version, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    decision.id,
                    decision.candidate_id,
                    decision.action.value,
                    decision.use,
                    decision.note,
                    decision.actor_id,
                    decision.expected_version,
                    now,
                ),
            )
            row = connection.execute(
                "SELECT * FROM candidates WHERE id = ?", (decision.candidate_id,)
            ).fetchone()
        return self._candidate_snapshot(row)

    def enqueue_outbox(self, task: NewOutboxTask) -> None:
        with self._transaction() as connection:
            self._insert_outbox(connection, task, _utc_now())

    def list_pending_outbox(self) -> list[OutboxSnapshot]:
        states = (
            OutboxState.PENDING.value,
            OutboxState.RETRYABLE_FAILURE.value,
            OutboxState.UNKNOWN_RESULT.value,
        )
        placeholders = ",".join("?" for _ in states)
        with self._read_connection() as connection:
            rows = connection.execute(
                f"""
                SELECT id, operation, state, attempts, batch_id, candidate_id
                FROM outbox_tasks
                WHERE state IN ({placeholders})
                ORDER BY created_at, id
                """,
                states,
            ).fetchall()
        return [
            OutboxSnapshot(
                id=row["id"],
                operation=row["operation"],
                state=OutboxState(row["state"]),
                attempts=row["attempts"],
                batch_id=row["batch_id"],
                candidate_id=row["candidate_id"],
            )
            for row in rows
        ]

    def append_audit_event(self, event: NewAuditEvent) -> None:
        with self._transaction() as connection:
            connection.execute(
                """
                INSERT INTO audit_events(
                    id, batch_id, candidate_id, event_type, actor_id, request_id,
                    payload_summary_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.id,
                    event.batch_id,
                    event.candidate_id,
                    event.event_type,
                    event.actor_id,
                    event.request_id,
                    _json(event.payload_summary),
                    _utc_now(),
                ),
            )

    @staticmethod
    def _insert_import_batch(
        connection: sqlite3.Connection, batch: NewImportBatch, now: str
    ) -> None:
        connection.execute(
            """
            INSERT INTO import_batches(
                id, project_id, status, idempotency_key, request_hash, created_by, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                batch.id,
                batch.project_id,
                batch.status.value,
                batch.idempotency_key,
                batch.request_hash,
                batch.created_by,
                now,
                now,
            ),
        )

    @staticmethod
    def _insert_outbox(
        connection: sqlite3.Connection, task: NewOutboxTask, now: str
    ) -> None:
        connection.execute(
            """
            INSERT INTO outbox_tasks(
                id, batch_id, candidate_id, operation, payload_json, state,
                attempts, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?)
            """,
            (
                task.id,
                task.batch_id,
                task.candidate_id,
                task.operation,
                _json(task.payload),
                OutboxState.PENDING.value,
                now,
                now,
            ),
        )

    @staticmethod
    def _raise_duplicate_request(error: sqlite3.IntegrityError) -> None:
        message = str(error)
        if "import_batches.project_id, import_batches.idempotency_key" in message:
            raise DuplicateRequest("import idempotency key already exists") from error
        raise error

    @staticmethod
    def _candidate_snapshot(row: sqlite3.Row | None) -> CandidateSnapshot:
        if row is None:
            raise LookupError("candidate not found")
        return CandidateSnapshot(
            id=row["id"],
            candidate_key=row["candidate_key"],
            content_hash=row["content_hash"],
            review_status=ReviewStatus(row["review_status"]),
            version=row["version"],
            scene=row["scene"],
            severity=row["severity"],
        )
