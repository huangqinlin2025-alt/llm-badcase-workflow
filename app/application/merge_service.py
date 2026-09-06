"""Application service for exact multi-file score merging."""
import json
from typing import Any
from uuid import uuid4

from app.domain.imports import ImportValidationError
from app.domain.merge import MergeValidationError, NewAlignedCase, PersistedSourceFile
from app.domain.models import NewParseIssue
from app.domain.status import ImportBatchStatus
from app.infrastructure.import_parser import ImportParser
from app.infrastructure.shopping_review_ab import PROFILE as SHOPPING_REVIEW_PROFILE
from app.infrastructure.sqlite_repository import SqliteWorkflowRepository


class MergeService:
    def __init__(self, repository: SqliteWorkflowRepository, parser: ImportParser) -> None:
        self._repository = repository
        self._parser = parser

    def merge_batch(self, batch_id: str) -> dict[str, object]:
        sources = self._repository.list_source_files(batch_id)
        if not sources:
            raise MergeValidationError("import batch has no persisted source files")
        profiles = {str(source.schema.get("profile", "generic-v1")) for source in sources}
        if SHOPPING_REVIEW_PROFILE in profiles:
            if profiles != {SHOPPING_REVIEW_PROFILE} or len(sources) != 1:
                raise MergeValidationError("shopping review paired report cannot be mixed with generic sources")
            try:
                aligned, issues = self._parser.load_shopping_review_paired(sources[0])
            except ImportValidationError as error:
                raise MergeValidationError(str(error)) from error
            status = ImportBatchStatus.PARTIAL_FAILURE if issues else ImportBatchStatus.ALIGNED
            self._repository.replace_aligned_cases(
                batch_id=batch_id,
                aligned_cases=aligned,
                issues=issues,
                status=status,
            )
            return self._repository.get_import_batch_report(batch_id)

        grouped: dict[tuple[str, str, str], list[tuple[PersistedSourceFile, dict[str, Any]]]] = {}
        for source in sources:
            try:
                records = self._parser.load_persisted_records(source.storage_path)
            except ImportValidationError as error:
                raise MergeValidationError(
                    f"source artifact is unavailable for {source.original_name}: {error}"
                ) from error
            for record in records:
                key = (str(record["case_id"]), source.side, source.evaluation_version)
                grouped.setdefault(key, []).append((source, record))

        aligned: list[NewAlignedCase] = []
        issues: list[NewParseIssue] = []
        for (case_id, side, evaluation_version), members in sorted(grouped.items()):
            payload, conflict = self._merge_members(case_id, side, evaluation_version, members)
            if conflict:
                issues.append(
                    NewParseIssue(
                        id=str(uuid4()),
                        batch_id=batch_id,
                        file_id=members[0][0].id,
                        code="MERGE_CONFLICT",
                        detail=conflict,
                    )
                )
                continue
            aligned.append(
                NewAlignedCase(
                    id=str(uuid4()),
                    batch_id=batch_id,
                    case_id=case_id,
                    side=side,
                    evaluation_version=evaluation_version,
                    payload=payload,
                )
            )

        status = ImportBatchStatus.PARTIAL_FAILURE if issues else ImportBatchStatus.ALIGNED
        self._repository.replace_aligned_cases(
            batch_id=batch_id,
            aligned_cases=aligned,
            issues=issues,
            status=status,
        )
        return self._repository.get_import_batch_report(batch_id)

    @staticmethod
    def _merge_members(
        case_id: str,
        side: str,
        evaluation_version: str,
        members: list[tuple[PersistedSourceFile, dict[str, Any]]],
    ) -> tuple[dict[str, object], str | None]:
        merged: dict[str, object] = {
            "case_id": case_id,
            "side": side,
            "evaluation_version": evaluation_version,
            "scene": "",
            "prev_output": "",
            "user_req": "",
            "cur_output": "",
            "scores": {},
            "source_file_ids": [],
        }
        scores: dict[str, Any] = merged["scores"]  # type: ignore[assignment]
        refs: list[str] = merged["source_file_ids"]  # type: ignore[assignment]

        for source, record in members:
            refs.append(source.id)
            for field in ("scene", "prev_output", "user_req", "cur_output"):
                incoming = str(record.get(field, "") or "")
                existing = str(merged[field])
                if incoming and existing and incoming != existing:
                    return {}, f"{case_id} has conflicting {field} in {source.original_name}"
                if incoming and not existing:
                    merged[field] = incoming
            for scorer, score in dict(record.get("scores", {})).items():
                existing_score = scores.get(scorer)
                if existing_score is not None and MergeService._score_values(existing_score) != MergeService._score_values(score):
                    return {}, f"{case_id} has conflicting {scorer} scores in {source.original_name}"
                if existing_score is None:
                    scores[scorer] = score
        return merged, None

    @staticmethod
    def _score_values(score: Any) -> str:
        values = score.get("values", []) if isinstance(score, dict) else []
        return json.dumps(values, ensure_ascii=False, separators=(",", ":"))
