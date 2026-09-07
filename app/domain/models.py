"""Framework-independent commands and records for the SQLite fact ledger."""
from dataclasses import dataclass
from typing import Mapping

from app.domain.status import ImportBatchStatus, OutboxState, ReviewStatus


@dataclass(frozen=True)
class NewImportBatch:
    id: str
    project_id: str
    idempotency_key: str
    request_hash: str
    created_by: str
    status: ImportBatchStatus = ImportBatchStatus.RECEIVED


@dataclass(frozen=True)
class NewSourceFile:
    id: str
    batch_id: str
    project_id: str
    sha256: str
    source_type: str
    side: str
    evaluation_version: str
    original_name: str
    storage_path: str
    schema: Mapping[str, object]
    rows_total: int
    rows_parsed: int


@dataclass(frozen=True)
class NewParseIssue:
    id: str
    batch_id: str
    file_id: str | None
    code: str
    detail: str
    row_no: int | None = None


@dataclass(frozen=True)
class NewOutboxTask:
    id: str
    operation: str
    payload: Mapping[str, object]
    batch_id: str | None = None
    candidate_id: str | None = None


@dataclass(frozen=True)
class NewCandidate:
    id: str
    batch_id: str
    candidate_key: str
    content_hash: str
    rule_set_version: str
    scene: str
    severity: str
    evidence: Mapping[str, object]


@dataclass(frozen=True)
class CandidateSnapshot:
    id: str
    candidate_key: str
    content_hash: str
    review_status: ReviewStatus
    version: int
    scene: str
    severity: str


@dataclass(frozen=True)
class NewReviewDecision:
    id: str
    candidate_id: str
    action: ReviewStatus
    actor_id: str
    expected_version: int
    use: str = ""
    note: str = ""


@dataclass(frozen=True)
class NewAuditEvent:
    id: str
    event_type: str
    actor_id: str
    request_id: str
    payload_summary: Mapping[str, object]
    batch_id: str | None = None
    candidate_id: str | None = None


@dataclass(frozen=True)
class OutboxSnapshot:
    id: str
    operation: str
    state: OutboxState
    attempts: int
    batch_id: str | None
    candidate_id: str | None
