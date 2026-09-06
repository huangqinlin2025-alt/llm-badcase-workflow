import asyncio
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from httpx import ASGITransport, AsyncClient

from app.infrastructure.config import Settings
from app.infrastructure.container import get_repository, get_settings
from app.main import app


async def submit_import(
    *,
    content: bytes,
    filename: str = "scores.csv",
    side: str = "B",
    evaluation_version: str = "eval-v1",
    idempotency_key: str = "import-key-0001",
) -> object:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        return await client.post(
            "/api/v1/import-batches",
            data={
                "project_id": "project-1",
                "sides": side,
                "evaluation_versions": evaluation_version,
            },
            files={"files": (filename, content, "text/csv")},
            headers={"Idempotency-Key": idempotency_key},
        )


class ImportApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = TemporaryDirectory()
        self.runtime = Path(self.temp_dir.name) / "runtime"
        self.artifacts = Path(self.temp_dir.name) / "artifacts"
        values = {
            "BADCASE_ENV": "test",
            "BADCASE_DATABASE_PATH": str(self.runtime / "badcase.db"),
            "BADCASE_ARTIFACTS_PATH": str(self.artifacts),
            "BADCASE_MAX_UPLOAD_BYTES": "1024",
            "BADCASE_MAX_UPLOAD_ROWS": "10",
            "BADCASE_MAX_UPLOAD_FILES": "2",
            "BADCASE_MAX_CELL_CHARS": "100",
            "BADCASE_MAX_XLSX_ENTRIES": "20",
            "BADCASE_MAX_XLSX_UNCOMPRESSED_BYTES": "2048",
        }
        self.settings = Settings.from_env(values)
        self.settings_patch = patch(
            "app.infrastructure.container.Settings.from_env", return_value=self.settings
        )
        self.settings_patch.start()
        get_settings.cache_clear()
        get_repository.cache_clear()

    def tearDown(self) -> None:
        get_settings.cache_clear()
        get_repository.cache_clear()
        self.settings_patch.stop()
        self.temp_dir.cleanup()

    def test_upload_persists_parse_report_and_replays_same_idempotency_key(self) -> None:
        content = (
            "case_id,场景,模型打分-D1,模型打分-D2,本轮输出\n"
            "case-1,全能帮写,4,8,示例输出\n"
            ",全能帮写,7,8,缺少用例 ID\n"
        ).encode()
        first = asyncio.run(submit_import(content=content))
        second = asyncio.run(submit_import(content=content))

        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 201)
        body = first.json()
        self.assertEqual(second.json()["import_id"], body["import_id"])
        self.assertEqual(body["status"], "PARSED")
        self.assertEqual(body["sources"][0]["side"], "B")
        self.assertEqual(body["sources"][0]["evaluation_version"], "eval-v1")
        self.assertEqual(body["sources"][0]["rows_total"], 2)
        self.assertEqual(body["sources"][0]["rows_parsed"], 1)
        self.assertEqual(body["issues"][0]["code"], "MISSING_CASE_ID")
        self.assertTrue(list(self.artifacts.glob("imports/*/*.csv")))

    def test_rejects_unsupported_extensions_before_persisting(self) -> None:
        response = asyncio.run(
            submit_import(content=b"case_id,score\ncase-1,8\n", filename="unsafe.xlsm")
        )
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["detail"], "only .csv, .tsv, and .xlsx files are allowed")
        self.assertIsNone(
            get_repository(get_settings()).get_import_batch_by_idempotency(
                "project-1", "import-key-0001"
            )
        )

    def test_rejects_missing_merge_metadata(self) -> None:
        response = asyncio.run(
            submit_import(content="case_id,模型打分-D1\ncase-1,8\n".encode(), side="")
        )
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["detail"], "side is required for every source file")

    def test_removes_artifact_when_parse_validation_fails(self) -> None:
        response = asyncio.run(submit_import(content=b"unknown_column,score\ncase-1,8\n"))
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["detail"], "case_id column could not be identified")
        self.assertFalse(list(self.artifacts.glob("imports/**/*")))

    def test_rejects_idempotency_key_reused_with_different_content(self) -> None:
        first = asyncio.run(submit_import(content="case_id,模型打分-D1\ncase-1,8\n".encode()))
        replay = asyncio.run(submit_import(content="case_id,模型打分-D1\ncase-1,4\n".encode()))
        self.assertEqual(first.status_code, 201)
        self.assertEqual(replay.status_code, 422)
        self.assertIn("idempotency key was reused", replay.json()["detail"])


if __name__ == "__main__":
    unittest.main()
