"""Exact multi-source merge models and errors."""
from dataclasses import dataclass
from typing import Mapping


class MergeValidationError(ValueError):
    """A persisted import batch cannot be merged safely."""


@dataclass(frozen=True)
class PersistedSourceFile:
    id: str
    batch_id: str
    side: str
    evaluation_version: str
    storage_path: str
    original_name: str
    schema: Mapping[str, object]


@dataclass(frozen=True)
class NewAlignedCase:
    id: str
    batch_id: str
    case_id: str
    side: str
    evaluation_version: str
    payload: Mapping[str, object]
