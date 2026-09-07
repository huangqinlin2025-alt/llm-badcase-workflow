"""Safe adapters around the legacy table parser for uploaded source files."""
from dataclasses import dataclass
from hashlib import sha256
from io import BytesIO
from pathlib import Path
import re
from uuid import uuid4
import zipfile

import importer as legacy_importer

from app.domain.imports import ImportValidationError, UploadedSource
from app.domain.merge import PersistedSourceFile
from app.domain.models import NewParseIssue, NewSourceFile
from app.infrastructure.config import Settings
from app.infrastructure.shopping_review_ab import PROFILE as SHOPPING_REVIEW_PROFILE
from app.infrastructure.shopping_review_ab import ShoppingReviewPairedAdapter

_ALLOWED_SUFFIXES = {".csv", ".tsv", ".xlsx"}
_SAFE_FILENAME = re.compile(r"[^A-Za-z0-9._-]+")


@dataclass(frozen=True)
class ParsedSource:
    source_file: NewSourceFile
    issues: list[NewParseIssue]


class ImportParser:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def parse(self, batch_id: str, project_id: str, upload: UploadedSource) -> ParsedSource:
        suffix = self._validate_metadata(upload)
        if len(upload.content) > self._settings.max_upload_bytes:
            raise ImportValidationError("uploaded file exceeds the configured size limit")
        if not upload.content:
            raise ImportValidationError("uploaded file is empty")
        if suffix == ".xlsx":
            self._validate_xlsx(upload.content)

        file_id = str(uuid4())
        storage_path = self._write_artifact(batch_id, file_id, upload.filename, upload.content)
        try:
            return self._parse_saved_artifact(
                batch_id=batch_id,
                project_id=project_id,
                file_id=file_id,
                suffix=suffix,
                upload=upload,
                storage_path=storage_path,
            )
        except Exception:
            self._remove_artifact(storage_path)
            raise

    def _parse_saved_artifact(
        self,
        *,
        batch_id: str,
        project_id: str,
        file_id: str,
        suffix: str,
        upload: UploadedSource,
        storage_path: Path,
    ) -> ParsedSource:
        if upload.profile == SHOPPING_REVIEW_PROFILE:
            return self._parse_shopping_review_artifact(
                batch_id=batch_id,
                project_id=project_id,
                file_id=file_id,
                suffix=suffix,
                upload=upload,
                storage_path=storage_path,
            )
        try:
            headers, rows = legacy_importer.read_table(str(storage_path))
        except (OSError, UnicodeDecodeError, ValueError, zipfile.BadZipFile) as error:
            raise ImportValidationError(f"unable to parse uploaded file: {error}") from error
        if not headers:
            raise ImportValidationError("uploaded file does not contain a header row")
        if len(rows) > self._settings.max_upload_rows:
            raise ImportValidationError("uploaded file exceeds the configured row limit")
        if any(len(str(cell)) > self._settings.max_cell_chars for row in rows for cell in row):
            raise ImportValidationError("uploaded file contains an oversized cell")

        schema = legacy_importer.detect_schema(headers, rows)
        if schema["id_col"] is None:
            raise ImportValidationError("case_id column could not be identified")
        records = [
            record
            for record in legacy_importer.extract(headers, rows, schema)
            if not record["case_id"].startswith("row#")
        ]

        issue_models: list[NewParseIssue] = []
        for row_number, row in enumerate(rows, start=2):
            case_id = row[schema["id_col"]] if schema["id_col"] < len(row) else ""
            if not str(case_id).strip():
                issue_models.append(
                    NewParseIssue(
                        id=str(uuid4()),
                        batch_id=batch_id,
                        file_id=file_id,
                        row_no=row_number,
                        code="MISSING_CASE_ID",
                        detail="row was excluded because the explicit case_id is empty",
                    )
                )

        summary = {"profile": upload.profile, **legacy_importer.summarize_schema(headers, schema, records)}
        source = NewSourceFile(
            id=file_id,
            batch_id=batch_id,
            project_id=project_id,
            sha256=sha256(upload.content).hexdigest(),
            source_type=suffix.lstrip("."),
            side=upload.side.strip(),
            evaluation_version=upload.evaluation_version.strip(),
            original_name=Path(upload.filename).name,
            storage_path=str(storage_path.relative_to(self._settings.artifacts_path)),
            schema=summary,
            rows_total=len(rows),
            rows_parsed=len(records),
        )
        return ParsedSource(source_file=source, issues=issue_models)

    def _parse_shopping_review_artifact(
        self,
        *,
        batch_id: str,
        project_id: str,
        file_id: str,
        suffix: str,
        upload: UploadedSource,
        storage_path: Path,
    ) -> ParsedSource:
        table = ShoppingReviewPairedAdapter().read_table(storage_path)
        if len(table.rows) > self._settings.max_upload_rows:
            raise ImportValidationError("uploaded file exceeds the configured row limit")
        summary = ShoppingReviewPairedAdapter().summarize(table)
        source = NewSourceFile(
            id=file_id,
            batch_id=batch_id,
            project_id=project_id,
            sha256=sha256(upload.content).hexdigest(),
            source_type=suffix.lstrip("."),
            side=upload.side.strip(),
            evaluation_version=upload.evaluation_version.strip(),
            original_name=Path(upload.filename).name,
            storage_path=str(storage_path.relative_to(self._settings.artifacts_path)),
            schema=summary,
            rows_total=len(table.rows),
            rows_parsed=int(summary["rows_parsed"]),
        )
        return ParsedSource(source_file=source, issues=[])

    def load_persisted_records(self, storage_path: str) -> list[dict[str, object]]:
        """Re-read a saved source without accepting arbitrary caller-controlled paths."""
        candidate = self._artifact_path(storage_path)
        try:
            headers, rows = legacy_importer.read_table(str(candidate))
        except (OSError, UnicodeDecodeError, ValueError, zipfile.BadZipFile) as error:
            raise ImportValidationError(f"unable to re-read persisted source: {error}") from error
        schema = legacy_importer.detect_schema(headers, rows)
        if schema["id_col"] is None:
            raise ImportValidationError("persisted source no longer has a case_id column")
        return [
            record
            for record in legacy_importer.extract(headers, rows, schema)
            if not record["case_id"].startswith("row#")
        ]

    def load_shopping_review_paired(
        self, source: PersistedSourceFile
    ) -> tuple[list, list[NewParseIssue]]:
        candidate = self._artifact_path(source.storage_path)
        return ShoppingReviewPairedAdapter().build_aligned_cases(source, candidate)

    def _artifact_path(self, storage_path: str) -> Path:
        candidate = (self._settings.artifacts_path / storage_path).resolve()
        root = self._settings.artifacts_path.resolve()
        if root not in candidate.parents or not candidate.is_file():
            raise ImportValidationError("persisted source artifact is unavailable")
        return candidate

    def _validate_metadata(self, upload: UploadedSource) -> str:
        filename = Path(upload.filename).name
        suffix = Path(filename).suffix.lower()
        if not filename or filename != upload.filename or suffix not in _ALLOWED_SUFFIXES:
            raise ImportValidationError("only .csv, .tsv, and .xlsx files are allowed")
        if not upload.side.strip():
            raise ImportValidationError("side is required for every source file")
        if not upload.evaluation_version.strip():
            raise ImportValidationError("evaluation_version is required for every source file")
        if upload.profile not in {"generic-v1", SHOPPING_REVIEW_PROFILE}:
            raise ImportValidationError("unsupported source profile")
        if upload.profile == SHOPPING_REVIEW_PROFILE and (suffix != ".xlsx" or upload.side.strip() != "PAIR"):
            raise ImportValidationError("shopping review paired profile requires an .xlsx file with side=PAIR")
        return suffix

    def _validate_xlsx(self, content: bytes) -> None:
        try:
            with zipfile.ZipFile(BytesIO(content)) as archive:
                info = archive.infolist()
                if len(info) > self._settings.max_xlsx_entries:
                    raise ImportValidationError("xlsx archive contains too many entries")
                uncompressed_size = sum(member.file_size for member in info)
                if uncompressed_size > self._settings.max_xlsx_uncompressed_bytes:
                    raise ImportValidationError("xlsx archive expands beyond the configured size limit")
                if any(
                    member.is_dir()
                    or member.filename.startswith("/")
                    or ".." in Path(member.filename).parts
                    for member in info
                ):
                    raise ImportValidationError("xlsx archive contains an invalid path")
        except zipfile.BadZipFile as error:
            raise ImportValidationError("uploaded .xlsx is not a valid ZIP archive") from error

    def _write_artifact(self, batch_id: str, file_id: str, filename: str, content: bytes) -> Path:
        safe_name = _SAFE_FILENAME.sub("_", Path(filename).name).strip("._") or "upload"
        target_dir = self._settings.artifacts_path / "imports" / batch_id
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / f"{file_id}_{safe_name}"
        resolved_root = self._settings.artifacts_path.resolve()
        if resolved_root not in target.resolve().parents:
            raise ImportValidationError("invalid artifact path")
        target.write_bytes(content)
        return target

    @staticmethod
    def _remove_artifact(storage_path: Path) -> None:
        storage_path.unlink(missing_ok=True)
        for directory in (storage_path.parent, storage_path.parent.parent):
            try:
                directory.rmdir()
            except OSError:
                break
