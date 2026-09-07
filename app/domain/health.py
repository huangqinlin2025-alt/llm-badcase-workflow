"""Health domain models with no dependency on HTTP or subprocess APIs."""
from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True)
class HealthReport:
    """A safe-to-expose snapshot of service readiness."""

    status: str
    checks: Mapping[str, str]

    def as_dict(self) -> dict[str, object]:
        return {"status": self.status, "checks": dict(self.checks)}
