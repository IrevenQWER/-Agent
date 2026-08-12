from datetime import date

import pytest

from futureedu_insight.adapters import SQLiteLearningDataGateway
from futureedu_insight.domain.errors import PermissionDeniedError
from futureedu_insight.domain.models import TeacherExecutionContext


def test_student_resolution_is_scoped_to_teacher_roster(
    gateway: SQLiteLearningDataGateway,
    teacher_context: TeacherExecutionContext,
) -> None:
    students = gateway.resolve_students("张晨", None, teacher_context)
    assert [student.student_id for student in students] == ["S1001"]


def test_other_teacher_resolves_only_their_same_name_student(
    gateway: SQLiteLearningDataGateway,
) -> None:
    context = TeacherExecutionContext(
        teacher_id="T2001",
        request_id="REQ-OTHER",
        session_id="SESSION-OTHER",
    )
    students = gateway.resolve_students("张晨", None, context)
    assert [student.student_id for student in students] == ["S2001"]


def test_every_data_query_rechecks_authorization(
    gateway: SQLiteLearningDataGateway,
    teacher_context: TeacherExecutionContext,
) -> None:
    with pytest.raises(PermissionDeniedError):
        gateway.get_score_records(
            "S2001",
            "数学",
            date(2026, 3, 1),
            date(2026, 4, 30),
            teacher_context,
        )


def test_score_query_returns_only_requested_period_and_subject(
    gateway: SQLiteLearningDataGateway,
    teacher_context: TeacherExecutionContext,
) -> None:
    records = gateway.get_score_records(
        "S1001",
        "数学",
        date(2026, 3, 20),
        date(2026, 4, 30),
        teacher_context,
    )
    assert [record.record_id for record in records] == ["SCORE-1001-02", "SCORE-1001-03"]
