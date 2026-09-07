"""Dependency factories shared by API routes and future workers."""
from functools import lru_cache

from app.infrastructure.config import Settings
from app.infrastructure.sqlite_repository import SqliteWorkflowRepository


@lru_cache
def get_settings() -> Settings:
    return Settings.from_env()


@lru_cache
def get_repository(settings: Settings) -> SqliteWorkflowRepository:
    """Create a migration-checked repository once per immutable settings object."""
    return SqliteWorkflowRepository(settings.database_path)
