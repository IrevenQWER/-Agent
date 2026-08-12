from datetime import date

import pytest

from futureedu_insight.adapters import McpLearningDataAdapter
from futureedu_insight.domain.errors import PermissionDeniedError
from futureedu_insight.domain.models import TeacherExecutionContext


def test_mcp_adapter_calls_real_stdio_server(database_path, teacher_context) -> None:
    adapter = McpLearningDataAdapter(database_path, teacher_context)

    profile = adapter.get_student_profile("S1001", teacher_context)
    records = adapter.get_score_records(
        "S1001", "数学", date(2026, 3, 1), date(2026, 4, 30), teacher_context
    )

    assert profile.student_id == "S1001"
    assert [item.record_id for item in records] == [
        "SCORE-1001-01",
        "SCORE-1001-02",
        "SCORE-1001-03",
    ]


def test_mcp_adapter_rejects_context_substitution(database_path, teacher_context) -> None:
    adapter = McpLearningDataAdapter(database_path, teacher_context)
    substituted = TeacherExecutionContext(
        teacher_id="T2001",
        request_id=teacher_context.request_id,
        session_id=teacher_context.session_id,
    )

    with pytest.raises(PermissionDeniedError):
        adapter.get_student_profile("S2001", substituted)
