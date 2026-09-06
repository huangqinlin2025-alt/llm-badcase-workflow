"""Application service for a non-sensitive health response."""
from app.domain.health import HealthReport
from app.infrastructure.health_probe import RuntimeProbe


class HealthService:
    def __init__(self, probe: RuntimeProbe) -> None:
        self._probe = probe

    def check(self) -> HealthReport:
        checks = self._probe.checks()
        required = ("configuration",)
        status = "ok" if all(checks[name] == "ok" for name in required) else "degraded"
        return HealthReport(status=status, checks=checks)
