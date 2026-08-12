import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

from futureedu_insight.api.main import create_app
from futureedu_insight.config import Settings
from futureedu_insight.domain.models import TaskStatus

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _client(database_path: Path) -> TestClient:
    settings = Settings(
        env="test",
        database_path=database_path,
        cases_path=PROJECT_ROOT / "data" / "cases",
        chroma_path=database_path.parent / "chroma",
        min_case_relevance=0.2,
    )
    return TestClient(create_app(settings))


def test_api_full_workflow_requires_teacher_confirmation(database_path) -> None:
    headers = {"X-Teacher-ID": "T1001"}
    with _client(database_path) as client:
        created = client.post(
            "/api/v1/analysis/tasks",
            headers=headers,
            json={
                "query": "生成学生 S1001 2026年3月数学学情报告",
                "session_id": "SESSION-E2E",
                "include_parent_summary": False,
            },
        )
        assert created.status_code == 202
        assert created.json()["status"] == TaskStatus.RUNNING

        task_id = created.json()["task_id"]
        task_response = client.get(f"/api/v1/analysis/tasks/{task_id}", headers=headers)
        assert task_response.status_code == 200
        task_body = task_response.json()
        assert task_body["task"]["status"] == TaskStatus.AWAITING_CONFIRMATION
        assert task_body["validation"]["passed"] is True
        assert task_body["execution"]["metrics"]["data_completeness"] == 1.0
        assert task_body["execution"]["retrieved_cases"][0]["similarity_reasons"]
        report_id = task_body["report"]["report_id"]

        evidence = client.get(f"/api/v1/reports/{report_id}/evidence", headers=headers)
        assert evidence.status_code == 200
        assert evidence.json()
        assert client.get(f"/api/v1/reports/{report_id}/export", headers=headers).status_code == 409

        confirmed = client.post(
            f"/api/v1/reports/{report_id}/confirmations",
            headers=headers,
            json={"action": "confirm", "comments": "已核对"},
        )
        assert confirmed.status_code == 200
        assert confirmed.json()["task_status"] == TaskStatus.COMPLETED

        completed = client.get(f"/api/v1/analysis/tasks/{task_id}", headers=headers)
        assert completed.json()["task"]["status"] == TaskStatus.COMPLETED
        exported = client.get(
            f"/api/v1/reports/{report_id}/export?format=markdown", headers=headers
        )
        assert exported.status_code == 200
        assert "学情报告" in exported.text
        assert exported.headers["content-disposition"].endswith('.md"')

    with sqlite3.connect(database_path) as connection:
        event_types = {
            row[0]
            for row in connection.execute(
                "SELECT event_type FROM audit_events WHERE resource_id IN (?, ?)",
                (task_id, report_id),
            ).fetchall()
        }
    assert {
        "task_created",
        "analysis_evidence_used",
        "report_confirmed",
        "report_exported",
    } <= event_types


def test_api_enforces_authentication_and_task_ownership(database_path) -> None:
    with _client(database_path) as client:
        assert client.get("/api/v1/analysis/tasks/UNKNOWN").status_code == 401
        created = client.post(
            "/api/v1/analysis/tasks",
            headers={"X-Teacher-ID": "T1001"},
            json={
                "query": "生成学生 S1001 2026年3月数学学情报告",
                "session_id": "SESSION-OWNER",
            },
        )
        task_id = created.json()["task_id"]

        denied = client.get(
            f"/api/v1/analysis/tasks/{task_id}",
            headers={"X-Teacher-ID": "T2001"},
        )
        assert denied.status_code == 403


def test_api_clarification_resumes_same_task(database_path) -> None:
    headers = {"X-Teacher-ID": "T1001"}
    with _client(database_path) as client:
        created = client.post(
            "/api/v1/analysis/tasks",
            headers=headers,
            json={
                "query": "分析学生 S1001 的数学学情",
                "session_id": "SESSION-CLARIFY",
            },
        )
        task_id = created.json()["task_id"]
        before = client.get(f"/api/v1/analysis/tasks/{task_id}", headers=headers).json()
        assert before["task"]["status"] == TaskStatus.NEEDS_CLARIFICATION

        resumed = client.post(
            f"/api/v1/analysis/tasks/{task_id}/clarifications",
            headers=headers,
            json={"additional_information": "时间是2026年3月"},
        )
        assert resumed.status_code == 202
        after = client.get(f"/api/v1/analysis/tasks/{task_id}", headers=headers).json()
        assert after["task"]["status"] == TaskStatus.AWAITING_CONFIRMATION


def test_api_revalidates_teacher_edits_before_confirmation(database_path) -> None:
    headers = {"X-Teacher-ID": "T1001"}
    with _client(database_path) as client:
        created = client.post(
            "/api/v1/analysis/tasks",
            headers=headers,
            json={
                "query": "生成学生 S1001 2026年3月数学学情报告",
                "session_id": "SESSION-EDIT",
            },
        )
        task_id = created.json()["task_id"]
        body = client.get(f"/api/v1/analysis/tasks/{task_id}", headers=headers).json()
        report = body["report"]
        report_id = report["report_id"]
        report["evidence"] = []

        rejected = client.post(
            f"/api/v1/reports/{report_id}/confirmations",
            headers=headers,
            json={"action": "confirm", "edited_report": report},
        )

        assert rejected.status_code == 422
        current = client.get(f"/api/v1/analysis/tasks/{task_id}", headers=headers).json()
        assert current["task"]["status"] == TaskStatus.AWAITING_CONFIRMATION
        assert current["report"]["evidence"]
