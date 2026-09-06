"""Application service for safe local file imports."""
from hashlib import sha256
from uuid import uuid4

from app.domain.errors import DuplicateRequest
from app.domain.imports import ImportValidationError, UploadedSource
from app.domain.models import NewImportBatch, NewParseIssue, NewSourceFile
from app.domain.status import ImportBatchStatus
from app.infrastructure.config import Settings
from app.infrastructure.import_parser import ImportParser, ParsedSource
from app.infrastructure.sqlite_repository import SqliteWorkflowRepository


class ImportService:
    def __init__(
        self,
        settings: Settings,
        repository: SqliteWorkflowRepository,
        parser: ImportParser | None = None,
    ) -> None:
        self._settings = settings
        self._repository = repository
        self._parser = parser or ImportParser(settings)

    def import_sources(
        self,
        *,
        project_id: str,
        actor_id: str,
        idempotency_key: str,
        sources: list[UploadedSource],
    ) -> dict[str, object]:
        if not sources:
            raise ImportValidationError("at least one source file is required")
        if len(sources) > self._settings.max_upload_files:
            raise ImportValidationError("too many source files")

        request_hash = self._request_hash(project_id, sources)
        existing = self._repository.get_import_batch_by_idempotency(project_id, idempotency_key)
        if existing is not None:
            if existing["request_hash"] != request_hash:
                raise ImportValidationError("idempotency key was reused with a different request")
            return self._repository.get_import_batch_report(existing["id"])

        batch_id = str(uuid4())
        parsed_sources: list[ParsedSource] = []
        source_models: list[NewSourceFile] = []
        issue_models: list[NewParseIssue] = []
        for source in sources:
            parsed = self._parser.parse(batch_id, project_id, source)
            parsed_sources.append(parsed)
            source_models.append(parsed.source_file)
            issue_models.extend(parsed.issues)

        if not any(item.source_file.rows_parsed for item in parsed_sources):
            raise ImportValidationError("no valid score records were found")

        batch = NewImportBatch(
            id=batch_id,
            project_id=project_id,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            created_by=actor_id,
            status=ImportBatchStatus.PARSED,
        )
        try:
            self._repository.create_import_batch_with_sources(
                batch=batch,
                source_files=source_models,
                issues=issue_models,
            )
        except DuplicateRequest as error:
            raise ImportValidationError("duplicate import request") from error
        return self._repository.get_import_batch_report(batch_id)

    @staticmethod
    def _request_hash(project_id: str, sources: list[UploadedSource]) -> str:
        material = [project_id]
        for source in sources:
            material.extend(
                (
                    source.filename,
                    source.side.strip(),
                    source.evaluation_version.strip(),
                    source.profile.strip(),
                    sha256(source.content).hexdigest(),
                )
            )
        return sha256("\u241f".join(material).encode("utf-8")).hexdigest()
