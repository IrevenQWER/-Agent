from __future__ import annotations

from pathlib import Path

import pytest

from futureedu_insight.adapters import SQLiteLearningDataGateway
from futureedu_insight.domain.models import TeacherExecutionContext
from futureedu_insight.infrastructure.seed import seed_database


@pytest.fixture
def database_path(tmp_path: Path) -> Path:
    path = tmp_path / "futureedu-test.db"
    seed_database(path, reset=True)
    return path


@pytest.fixture
def gateway(database_path: Path) -> SQLiteLearningDataGateway:
    return SQLiteLearningDataGateway(database_path)


@pytest.fixture
def teacher_context() -> TeacherExecutionContext:
    return TeacherExecutionContext(
        teacher_id="T1001",
        request_id="REQ-TEST-001",
        session_id="SESSION-TEST-001",
    )
