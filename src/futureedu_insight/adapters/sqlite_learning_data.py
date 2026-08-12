from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date
from pathlib import Path

from futureedu_insight.domain.errors import PermissionDeniedError, StudentNotFoundError
from futureedu_insight.domain.models import (
    AttendanceRecord,
    ClassroomFeedback,
    HomeworkRecord,
    ScoreRecord,
    StudentProfile,
    TeacherExecutionContext,
)


class SQLiteLearningDataGateway:
    """SQLite adapter with object-level authorization on every query."""

    def __init__(self, database_path: Path | str) -> None:
        self.database_path = Path(database_path)

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
        finally:
            connection.close()

    def _assert_authorized(
        self,
        connection: sqlite3.Connection,
        student_id: str,
        context: TeacherExecutionContext,
    ) -> None:
        row = connection.execute(
            """
            SELECT 1
            FROM teacher_student_access
            WHERE teacher_id = ? AND student_id = ?
            LIMIT 1
            """,
            (context.teacher_id, student_id),
        ).fetchone()
        if row is None:
            raise PermissionDeniedError("无权访问该学生")

    @staticmethod
    def _student_from_row(row: sqlite3.Row) -> StudentProfile:
        return StudentProfile(
            student_id=row["student_id"],
            display_name=row["display_name"],
            grade=row["grade"],
            class_id=row["class_id"],
            class_name=row["class_name"],
            campus_id=row["campus_id"],
            enrollment_status=row["enrollment_status"],
        )

    def resolve_students(
        self,
        identifier: str,
        class_context: str | None,
        context: TeacherExecutionContext,
    ) -> list[StudentProfile]:
        """Resolve only within the authenticated teacher's authorized roster."""

        normalized = identifier.strip()
        like_value = f"%{normalized}%"
        query = """
            SELECT s.*
            FROM students s
            JOIN teacher_student_access a ON a.student_id = s.student_id
            WHERE a.teacher_id = ?
              AND s.enrollment_status = 'active'
              AND (s.student_id = ? OR s.display_name LIKE ?)
        """
        parameters: list[str] = [context.teacher_id, normalized, like_value]
        if class_context:
            query += " AND (s.class_id = ? OR s.class_name = ?)"
            parameters.extend([class_context, class_context])
        query += " ORDER BY s.student_id LIMIT 20"

        with self._connection() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return [self._student_from_row(row) for row in rows]

    def get_student_profile(
        self,
        student_id: str,
        context: TeacherExecutionContext,
    ) -> StudentProfile:
        with self._connection() as connection:
            self._assert_authorized(connection, student_id, context)
            row = connection.execute(
                "SELECT * FROM students WHERE student_id = ? AND enrollment_status = 'active'",
                (student_id,),
            ).fetchone()
        if row is None:
            raise StudentNotFoundError(student_id)
        return self._student_from_row(row)

    def get_score_records(
        self,
        student_id: str,
        subject: str,
        start_date: date,
        end_date: date,
        context: TeacherExecutionContext,
    ) -> list[ScoreRecord]:
        with self._connection() as connection:
            self._assert_authorized(connection, student_id, context)
            rows = connection.execute(
                """
                SELECT * FROM scores
                WHERE student_id = ? AND subject = ? AND exam_date BETWEEN ? AND ?
                ORDER BY exam_date, record_id
                """,
                (student_id, subject, start_date.isoformat(), end_date.isoformat()),
            ).fetchall()
        return [
            ScoreRecord(
                record_id=row["record_id"],
                student_id=row["student_id"],
                exam_id=row["exam_id"],
                exam_name=row["exam_name"],
                subject=row["subject"],
                score=row["score"],
                full_score=row["full_score"],
                class_average=row["class_average"],
                rank=row["rank"],
                participant_count=row["participant_count"],
                exam_date=date.fromisoformat(row["exam_date"]),
                knowledge_scores=json.loads(row["knowledge_scores"]),
            )
            for row in rows
        ]

    def get_homework_records(
        self,
        student_id: str,
        subject: str,
        start_date: date,
        end_date: date,
        context: TeacherExecutionContext,
    ) -> list[HomeworkRecord]:
        with self._connection() as connection:
            self._assert_authorized(connection, student_id, context)
            rows = connection.execute(
                """
                SELECT * FROM homework_records
                WHERE student_id = ? AND subject = ? AND homework_date BETWEEN ? AND ?
                ORDER BY homework_date, record_id
                """,
                (student_id, subject, start_date.isoformat(), end_date.isoformat()),
            ).fetchall()
        return [
            HomeworkRecord(
                record_id=row["record_id"],
                student_id=row["student_id"],
                subject=row["subject"],
                homework_date=date.fromisoformat(row["homework_date"]),
                submitted=bool(row["submitted"]),
                accuracy_rate=row["accuracy_rate"],
                corrected=None if row["corrected"] is None else bool(row["corrected"]),
                knowledge_tags=json.loads(row["knowledge_tags"]),
                teacher_comment=row["teacher_comment"],
            )
            for row in rows
        ]

    def get_attendance_records(
        self,
        student_id: str,
        start_date: date,
        end_date: date,
        context: TeacherExecutionContext,
    ) -> list[AttendanceRecord]:
        with self._connection() as connection:
            self._assert_authorized(connection, student_id, context)
            rows = connection.execute(
                """
                SELECT * FROM attendance_records
                WHERE student_id = ? AND lesson_date BETWEEN ? AND ?
                ORDER BY lesson_date, record_id
                """,
                (student_id, start_date.isoformat(), end_date.isoformat()),
            ).fetchall()
        return [
            AttendanceRecord(
                record_id=row["record_id"],
                student_id=row["student_id"],
                course_id=row["course_id"],
                lesson_date=date.fromisoformat(row["lesson_date"]),
                status=row["status"],
                late_minutes=row["late_minutes"],
                leave_type=row["leave_type"],
            )
            for row in rows
        ]

    def get_classroom_feedback(
        self,
        student_id: str,
        subject: str,
        start_date: date,
        end_date: date,
        context: TeacherExecutionContext,
    ) -> list[ClassroomFeedback]:
        with self._connection() as connection:
            self._assert_authorized(connection, student_id, context)
            rows = connection.execute(
                """
                SELECT * FROM classroom_feedback
                WHERE student_id = ? AND subject = ? AND feedback_date BETWEEN ? AND ?
                ORDER BY feedback_date, record_id
                """,
                (student_id, subject, start_date.isoformat(), end_date.isoformat()),
            ).fetchall()
        return [
            ClassroomFeedback(
                record_id=row["record_id"],
                student_id=row["student_id"],
                teacher_id=row["teacher_id"],
                course_id=row["course_id"],
                feedback_date=date.fromisoformat(row["feedback_date"]),
                performance_tags=json.loads(row["performance_tags"]),
                feedback_text=row["feedback_text"],
            )
            for row in rows
        ]
