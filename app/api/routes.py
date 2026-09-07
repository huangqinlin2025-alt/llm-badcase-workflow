"""HTTP routes. No business workflow logic belongs in this module."""
from fastapi import APIRouter

from app.application.health_service import HealthService
from app.infrastructure.config import Settings
from app.infrastructure.health_probe import RuntimeProbe

router = APIRouter()


def build_health_service(settings: Settings) -> HealthService:
    return HealthService(RuntimeProbe(settings))


@router.get("/healthz", tags=["system"])
def healthz() -> dict[str, object]:
    """Return safe process health without exposing configuration values."""
    from app.infrastructure.container import get_settings

    return build_health_service(get_settings()).check().as_dict()
