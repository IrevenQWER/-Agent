from __future__ import annotations

from datetime import date
from typing import Protocol

from futureedu_insight.domain.models import (
    AttendanceRecord,
    ClassroomFeedback,
    HomeworkRecord,
    ScoreRecord,
    StudentProfile,
    TeacherExecutionContext,
)


class LearningDataGateway(Protocol):
    """Protocol-independent access to authorized learning data."""

    def resolve_students(
        self,
        identifier: str,
        class_context: str | None,
        context: TeacherExecutionContext,
    ) -> list[StudentProfile]: ...

    def get_student_profile(
        self,
        student_id: str,
        context: TeacherExecutionContext,
    ) -> StudentProfile: ...

    def get_score_records(
        self,
        student_id: str,
        subject: str,
        start_date: date,
        end_date: date,
        context: TeacherExecutionContext,
    ) -> list[ScoreRecord]: ...

    def get_homework_records(
        self,
        student_id: str,
        subject: str,
        start_date: date,
        end_date: date,
        context: TeacherExecutionContext,
    ) -> list[HomeworkRecord]: ...

    def get_attendance_records(
        self,
        student_id: str,
        start_date: date,
        end_date: date,
        context: TeacherExecutionContext,
    ) -> list[AttendanceRecord]: ...

    def get_classroom_feedback(
        self,
        student_id: str,
        subject: str,
        start_date: date,
        end_date: date,
        context: TeacherExecutionContext,
    ) -> list[ClassroomFeedback]: ...
