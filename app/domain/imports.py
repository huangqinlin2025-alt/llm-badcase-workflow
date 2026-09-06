"""Import-specific domain inputs and validation errors."""
from dataclasses import dataclass


class ImportValidationError(ValueError):
    """A client-provided source does not satisfy the frozen import contract."""


@dataclass(frozen=True)
class UploadedSource:
    filename: str
    content: bytes
    side: str
    evaluation_version: str
    profile: str = "generic-v1"
