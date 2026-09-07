"""Ports consumed by future application services."""
from typing import Protocol

from app.domain.models import (
    CandidateSnapshot,
    NewCandidate,
    NewImportBatch,
    NewOutboxTask,
    NewReviewDecision,
    OutboxSnapshot,
)


class WorkflowRepository(Protocol):
    def create_import_batch_with_outbox(
        self, batch: NewImportBatch, task: NewOutboxTask
    ) -> None: ...

    def upsert_candidate(self, candidate: NewCandidate) -> CandidateSnapshot: ...

    def review_candidate(self, decision: NewReviewDecision) -> CandidateSnapshot: ...

    def list_pending_outbox(self) -> list[OutboxSnapshot]: ...
