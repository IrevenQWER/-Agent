from pathlib import Path

from fastapi.testclient import TestClient

from futureedu_insight.api.main import create_app
from futureedu_insight.config import Settings

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_api_can_run_full_analysis_through_stdio_mcp(database_path) -> None:
    settings = Settings(
        env="test",
        database_path=database_path,
        cases_path=PROJECT_ROOT / "data" / "cases",
        chroma_path=database_path.parent / "mcp-chroma",
        learning_data_backend="mcp_stdio",
        min_case_relevance=0.2,
    )
    headers = {"X-Teacher-ID": "T1001"}

    with TestClient(create_app(settings)) as client:
        created = client.post(
            "/api/v1/analysis/tasks",
            headers=headers,
            json={
                "query": "生成学生 S1001 2026年3月数学学情报告",
                "session_id": "SESSION-MCP-E2E",
            },
        )
        task = client.get(
            f"/api/v1/analysis/tasks/{created.json()['task_id']}", headers=headers
        ).json()

    assert task["task"]["status"] == "awaiting_confirmation"
    assert task["report"]["student_id"] == "S1001"
