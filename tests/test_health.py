import asyncio
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from httpx import ASGITransport, AsyncClient

from app.infrastructure.config import Settings
from app.infrastructure.container import get_repository
from app.main import app, get_settings


async def request_health() -> object:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        return await client.get("/healthz")


async def start_application() -> None:
    async with app.router.lifespan_context(app):
        pass


class HealthEndpointTests(unittest.TestCase):
    def setUp(self) -> None:
        get_settings.cache_clear()
        get_repository.cache_clear()

    def tearDown(self) -> None:
        get_settings.cache_clear()
        get_repository.cache_clear()

    def test_healthz_returns_safe_runtime_checks(self) -> None:
        with TemporaryDirectory() as temp_dir:
            runtime = Path(temp_dir) / "runtime"
            artifacts = Path(temp_dir) / "artifacts"
            runtime.mkdir()
            artifacts.mkdir()
            values = {
                "BADCASE_ENV": "test",
                "BADCASE_DATABASE_PATH": str(runtime / "badcase.db"),
                "BADCASE_ARTIFACTS_PATH": str(artifacts),
                "BADCASE_WECOM_CLI": "missing-wecom-cli",
                "BADCASE_WECOM_TIMEOUT_SECONDS": "15",
            }
            with patch("app.infrastructure.container.Settings.from_env", return_value=Settings.from_env(values)):
                get_settings.cache_clear()
                response = asyncio.run(request_health())

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")
        self.assertEqual(response.json()["checks"]["configuration"], "ok")
        self.assertEqual(response.json()["checks"]["database"], "pending_migration")
        self.assertEqual(response.json()["checks"]["artifacts"], "ready")
        self.assertEqual(response.json()["checks"]["wecom_cli"], "not_available")
        self.assertNotIn("database_path", response.text)

    def test_lifespan_applies_sqlite_migrations(self) -> None:
        with TemporaryDirectory() as temp_dir:
            database_path = Path(temp_dir) / "runtime" / "badcase.db"
            values = {
                "BADCASE_ENV": "test",
                "BADCASE_DATABASE_PATH": str(database_path),
                "BADCASE_ARTIFACTS_PATH": str(Path(temp_dir) / "artifacts"),
            }
            with patch("app.infrastructure.container.Settings.from_env", return_value=Settings.from_env(values)):
                get_settings.cache_clear()
                get_repository.cache_clear()
                asyncio.run(start_application())

            self.assertTrue(database_path.exists())


class SettingsTests(unittest.TestCase):
    def test_rejects_invalid_timeout(self) -> None:
        with self.assertRaises(ValueError):
            Settings.from_env({"BADCASE_WECOM_TIMEOUT_SECONDS": "0"})

    def test_rejects_unknown_environment(self) -> None:
        with self.assertRaises(ValueError):
            Settings.from_env({"BADCASE_ENV": "staging"})


if __name__ == "__main__":
    unittest.main()
