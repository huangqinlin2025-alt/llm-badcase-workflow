"""System probes used by the health application service."""
from dataclasses import dataclass
from pathlib import Path
from shutil import which

from app.infrastructure.config import Settings


@dataclass(frozen=True)
class RuntimeProbe:
    """Read-only checks that never invoke external side effects."""

    settings: Settings

    def checks(self) -> dict[str, str]:
        database_parent = self.settings.database_path.parent
        artifact_parent = self.settings.artifacts_path
        database_ready = self.settings.database_path.exists()
        return {
            "configuration": "ok",
            "database": "ready" if database_ready else (
                "pending_migration" if database_parent.exists() else "parent_missing"
            ),
            "artifacts": "ready" if artifact_parent.exists() else "pending_directory",
            "wecom_cli": "available" if which(self.settings.wecom_cli) else "not_available",
        }
