"""Inbound API for safe multipart file imports and exact merge operations."""
from fastapi import APIRouter, File, Form, Header, HTTPException, UploadFile, status

from app.application.import_service import ImportService
from app.application.merge_service import MergeService
from app.domain.imports import ImportValidationError, UploadedSource
from app.domain.merge import MergeValidationError
from app.infrastructure.container import get_repository, get_settings
from app.infrastructure.import_parser import ImportParser

router = APIRouter(prefix="/api/v1", tags=["imports"])


@router.post("/import-batches", status_code=status.HTTP_201_CREATED)
async def create_import_batch(
    files: list[UploadFile] = File(...),
    sides: list[str] = Form(...),
    evaluation_versions: list[str] = Form(...),
    source_profiles: list[str] | None = Form(default=None),
    project_id: str = Form("default"),
    authorization: str | None = Header(default=None),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> dict[str, object]:
    settings = get_settings()
    _require_token(settings.import_token, authorization)
    _require_idempotency_key(idempotency_key)
    if not project_id.strip():
        raise HTTPException(status_code=422, detail="project_id is required")
    profiles = source_profiles or ["generic-v1"] * len(files)
    if len(files) != len(sides) or len(files) != len(evaluation_versions) or len(files) != len(profiles):
        raise HTTPException(
            status_code=422,
            detail="files, sides, evaluation_versions, and source_profiles must have matching lengths",
        )

    sources: list[UploadedSource] = []
    for file, side, evaluation_version, profile in zip(files, sides, evaluation_versions, profiles, strict=True):
        content = await _read_limited(file, settings.max_upload_bytes)
        sources.append(
            UploadedSource(
                filename=file.filename or "",
                content=content,
                side=side,
                evaluation_version=evaluation_version,
                profile=profile,
            )
        )
    try:
        service = ImportService(settings, get_repository(settings))
        return service.import_sources(
            project_id=project_id.strip(),
            actor_id=_actor_id(authorization),
            idempotency_key=idempotency_key,
            sources=sources,
        )
    except ImportValidationError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.get("/import-batches/{batch_id}")
def get_import_batch(batch_id: str, authorization: str | None = Header(default=None)) -> dict[str, object]:
    settings = get_settings()
    _require_token(settings.import_token, authorization)
    try:
        return get_repository(settings).get_import_batch_report(batch_id)
    except LookupError as error:
        raise HTTPException(status_code=404, detail="import batch not found") from error


@router.post("/import-batches/{batch_id}/merge")
def merge_import_batch(
    batch_id: str,
    authorization: str | None = Header(default=None),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> dict[str, object]:
    settings = get_settings()
    _require_token(settings.import_token, authorization)
    _require_idempotency_key(idempotency_key)
    try:
        return MergeService(
            repository=get_repository(settings),
            parser=ImportParser(settings),
        ).merge_batch(batch_id)
    except LookupError as error:
        raise HTTPException(status_code=404, detail="import batch not found") from error
    except MergeValidationError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


async def _read_limited(upload: UploadFile, limit: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while chunk := await upload.read(64 * 1024):
        total += len(chunk)
        if total > limit:
            raise HTTPException(status_code=413, detail="uploaded file exceeds the configured size limit")
        chunks.append(chunk)
    return b"".join(chunks)


def _require_token(expected_token: str, authorization: str | None) -> None:
    if not expected_token:
        return
    if authorization != f"Bearer {expected_token}":
        raise HTTPException(status_code=401, detail="invalid import token")


def _require_idempotency_key(idempotency_key: str | None) -> None:
    if not idempotency_key or not 8 <= len(idempotency_key) <= 128:
        raise HTTPException(status_code=400, detail="Idempotency-Key must be 8-128 characters")


def _actor_id(authorization: str | None) -> str:
    return "token-importer" if authorization else "local-importer"
