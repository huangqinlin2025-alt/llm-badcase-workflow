"""Runtime configuration loaded only from the process environment."""
from dataclasses import dataclass
from os import environ
from pathlib import Path
from typing import Mapping


@dataclass(frozen=True)
class Settings:
    environment: str
    database_path: Path
    artifacts_path: Path
    wecom_cli: str
    wecom_timeout_seconds: int
    import_token: str
    max_upload_bytes: int
    max_upload_rows: int
    max_upload_files: int
    max_cell_chars: int
    max_xlsx_entries: int
    max_xlsx_uncompressed_bytes: int

    @classmethod
    def from_env(cls, values: Mapping[str, str] | None = None) -> "Settings":
        source = environ if values is None else values
        timeout = int(source.get("BADCASE_WECOM_TIMEOUT_SECONDS", "15"))
        if timeout < 1 or timeout > 120:
            raise ValueError("BADCASE_WECOM_TIMEOUT_SECONDS must be between 1 and 120")

        limits = {
            "max_upload_bytes": int(source.get("BADCASE_MAX_UPLOAD_BYTES", str(10 * 1024 * 1024))),
            "max_upload_rows": int(source.get("BADCASE_MAX_UPLOAD_ROWS", "10000")),
            "max_upload_files": int(source.get("BADCASE_MAX_UPLOAD_FILES", "10")),
            "max_cell_chars": int(source.get("BADCASE_MAX_CELL_CHARS", "10000")),
            "max_xlsx_entries": int(source.get("BADCASE_MAX_XLSX_ENTRIES", "1000")),
            "max_xlsx_uncompressed_bytes": int(
                source.get("BADCASE_MAX_XLSX_UNCOMPRESSED_BYTES", str(50 * 1024 * 1024))
            ),
        }
        if any(value < 1 for value in limits.values()):
            raise ValueError("all import limits must be positive")

        environment = source.get("BADCASE_ENV", "development").strip().lower()
        if environment not in {"development", "test", "production"}:
            raise ValueError("BADCASE_ENV must be development, test, or production")

        return cls(
            environment=environment,
            database_path=Path(source.get("BADCASE_DATABASE_PATH", "runtime/badcase.db")),
            artifacts_path=Path(source.get("BADCASE_ARTIFACTS_PATH", "artifacts")),
            wecom_cli=source.get("BADCASE_WECOM_CLI", "wecom-cli").strip(),
            wecom_timeout_seconds=timeout,
            import_token=source.get("BADCASE_IMPORT_TOKEN", ""),
            **limits,
        )
