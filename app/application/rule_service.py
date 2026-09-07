"""Application service that persists L1/L2/L3 rule candidates."""
from uuid import uuid4

from app.domain.models import NewCandidate
from app.domain.rules import RULE_SET_VERSION, evaluate_layered
from app.infrastructure.sqlite_repository import SqliteWorkflowRepository


class RuleService:
    def __init__(self, repository: SqliteWorkflowRepository) -> None:
        self._repository = repository

    def evaluate_batch(self, batch_id: str) -> dict[str, object]:
        aligned = self._repository.list_aligned_payloads(batch_id)
        if not aligned:
            raise ValueError("import batch has no aligned cases")
        decisions = evaluate_layered(aligned)
        self._repository.delete_pending_candidates(batch_id, RULE_SET_VERSION)
        for decision in decisions:
            self._repository.upsert_candidate(
                NewCandidate(
                    id=str(uuid4()),
                    batch_id=batch_id,
                    candidate_key=decision.candidate_key,
                    content_hash=decision.content_hash,
                    rule_set_version=RULE_SET_VERSION,
                    scene=decision.scene,
                    severity=decision.severity,
                    evidence=decision.evidence,
                )
            )
        self._repository.set_import_batch_status(batch_id, "DECIDED")
        return self._repository.get_import_batch_report(batch_id)
