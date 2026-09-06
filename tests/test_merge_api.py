import asyncio
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from httpx import ASGITransport, AsyncClient

from app.infrastructure.config import Settings
from app.infrastructure.container import get_repository, get_settings
from app.main import app


async def submit_sources(sources: list[tuple[str, bytes]], key: str) -> object:
    transport = ASGITransport(app=app)
    data = {
        "project_id": "project-1",
        "sides": ["B"] * len(sources),
        "evaluation_versions": ["eval-v1"] * len(sources),
    }
    files = [("files", (filename, content, "text/csv")) for filename, content in sources]
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        return await client.post(
            "/api/v1/import-batches",
            data=data,
            files=files,
            headers={"Idempotency-Key": key},
        )


async def merge_batch(batch_id: str, key: str = "merge-key-0001") -> object:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        return await client.post(
            f"/api/v1/import-batches/{batch_id}/merge",
            headers={"Idempotency-Key": key},
        )


class MergeApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = TemporaryDirectory()
        values = {
            "BADCASE_ENV": "test",
            "BADCASE_DATABASE_PATH": str(Path(self.temp_dir.name) / "runtime" / "badcase.db"),
            "BADCASE_ARTIFACTS_PATH": str(Path(self.temp_dir.name) / "artifacts"),
            "BADCASE_MAX_UPLOAD_BYTES": "4096",
            "BADCASE_MAX_UPLOAD_ROWS": "20",
            "BADCASE_MAX_UPLOAD_FILES": "5",
            "BADCASE_MAX_CELL_CHARS": "100",
            "BADCASE_MAX_XLSX_ENTRIES": "20",
            "BADCASE_MAX_XLSX_UNCOMPRESSED_BYTES": "4096",
        }
        self.settings_patch = patch(
            "app.infrastructure.container.Settings.from_env",
            return_value=Settings.from_env(values),
        )
        self.settings_patch.start()
        get_settings.cache_clear()
        get_repository.cache_clear()

    def tearDown(self) -> None:
        get_settings.cache_clear()
        get_repository.cache_clear()
        self.settings_patch.stop()
        self.temp_dir.cleanup()

    def test_exact_three_source_merge_preserves_partial_coverage_model(self) -> None:
        shared = "case_id,场景,上一轮输出,用户要求,本轮输出,{}\ncase-1,全能帮写,prev,req,cur,{}\n"
        response = asyncio.run(
            submit_sources(
                [
                    ("human.csv", shared.format("人工-D1", "7").encode()),
                    ("llm.csv", shared.format("LLM-D1", "4").encode()),
                    ("machine.csv", shared.format("机检-D1", "5").encode()),
                ],
                "import-key-merge-success",
            )
        )
        self.assertEqual(response.status_code, 201)

        merged = asyncio.run(merge_batch(response.json()["import_id"]))
        self.assertEqual(merged.status_code, 200)
        body = merged.json()
        self.assertEqual(body["status"], "ALIGNED")
        self.assertEqual(body["aligned"], 1)
        self.assertEqual(body["coverage"], {"LLM+人工+机检": 1})
        self.assertEqual(body["aligned_cases"][0]["case_id"], "case-1")
        self.assertEqual(body["aligned_cases"][0]["side"], "B")
        self.assertEqual(body["aligned_cases"][0]["evaluation_version"], "eval-v1")
        self.assertEqual(set(body["aligned_cases"][0]["scores"]), {"人工", "LLM", "机检"})

    def test_conflicting_scores_create_report_and_skip_aligned_case(self) -> None:
        template = "case_id,场景,LLM-D1\ncase-1,全能帮写,{}\n"
        response = asyncio.run(
            submit_sources(
                [
                    ("llm-first.csv", template.format("8").encode()),
                    ("llm-conflict.csv", template.format("4").encode()),
                ],
                "import-key-merge-conflict",
            )
        )
        self.assertEqual(response.status_code, 201)

        merged = asyncio.run(merge_batch(response.json()["import_id"], "merge-key-0002"))
        self.assertEqual(merged.status_code, 200)
        body = merged.json()
        self.assertEqual(body["status"], "PARTIAL_FAILURE")
        self.assertEqual(body["aligned"], 0)
        self.assertEqual(body["coverage"], {})
        self.assertEqual(body["issues"][0]["code"], "MERGE_CONFLICT")
        self.assertIn("conflicting LLM scores", body["issues"][0]["detail"])


if __name__ == "__main__":
    unittest.main()
