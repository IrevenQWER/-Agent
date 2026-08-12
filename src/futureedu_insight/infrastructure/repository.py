from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from futureedu_insight.domain.errors import PermissionDeniedError, StudentNotFoundError
from futureedu_insight.domain.models import (
    ConfirmationStatus,
    LearningReport,
    ReportRecord,
    ReportValidationResult,
    TaskRecord,
    TaskStatus,
    TeacherExecutionContext,
)


def utc_iso() -> str:
    return datetime.now(UTC).isoformat()


class SQLiteTaskRepository:
    """Owner-scoped task/report persistence and privacy-minimized audit events."""

    def __init__(self, database_path: Path | str) -> None:
        self.database_path = Path(database_path)

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def audit(
        self,
        context: TeacherExecutionContext,
        event_type: str,
        resource_type: str,
        resource_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO audit_events (
                    request_id, teacher_id, event_type, resource_type,
                    resource_id, metadata_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    context.request_id,
                    context.teacher_id,
                    event_type,
                    resource_type,
                    resource_id,
                    json.dumps(metadata or {}, ensure_ascii=False),
                    utc_iso(),
                ),
            )

    def create_task(
        self,
        task_id: str,
        query: str,
        include_parent_summary: bool,
        context: TeacherExecutionContext,
    ) -> TaskRecord:
        now = utc_iso()
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO analysis_tasks (
                    task_id, request_id, session_id, teacher_id, query,
                    include_parent_summary, status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task_id,
                    context.request_id,
                    context.session_id,
                    context.teacher_id,
                    query,
                    int(include_parent_summary),
                    TaskStatus.RUNNING.value,
                    now,
                    now,
                ),
            )
        self.audit(context, "task_created", "task", task_id)
        return self.get_task(task_id, context.teacher_id)

    def get_task_row(self, task_id: str, teacher_id: str) -> sqlite3.Row:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM analysis_tasks WHERE task_id = ? AND teacher_id = ?",
                (task_id, teacher_id),
            ).fetchone()
            exists = connection.execute(
                "SELECT 1 FROM analysis_tasks WHERE task_id = ?", (task_id,)
            ).fetchone()
        if row is None:
            if exists:
                raise PermissionDeniedError("无权访问该任务")
            raise StudentNotFoundError("任务不存在")
        return row

    def get_task(self, task_id: str, teacher_id: str) -> TaskRecord:
        row = self.get_task_row(task_id, teacher_id)
        return TaskRecord(
            task_id=row["task_id"],
            request_id=row["request_id"],
            session_id=row["session_id"],
            teacher_id=row["teacher_id"],
            status=row["status"],
            report_id=row["report_id"],
            clarification=(
                json.loads(row["clarification_json"]) if row["clarification_json"] else None
            ),
            error_code=row["error_code"],
            error_message=row["error_message"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    def task_input(
        self, task_id: str, teacher_id: str
    ) -> tuple[str, bool, TeacherExecutionContext]:
        row = self.get_task_row(task_id, teacher_id)
        return (
            row["query"],
            bool(row["include_parent_summary"]),
            TeacherExecutionContext(
                teacher_id=row["teacher_id"],
                request_id=row["request_id"],
                session_id=row["session_id"],
            ),
        )

    def restart_task(
        self,
        task_id: str,
        context: TeacherExecutionContext,
        *,
        query: str | None = None,
    ) -> None:
        self.get_task_row(task_id, context.teacher_id)
        with self._connection() as connection:
            if query is None:
                connection.execute(
                    """
                    UPDATE analysis_tasks SET status = ?, report_id = NULL,
                        clarification_json = NULL, execution_json = NULL,
                        error_code = NULL, error_message = NULL,
                        updated_at = ? WHERE task_id = ? AND teacher_id = ?
                    """,
                    (TaskStatus.RUNNING.value, utc_iso(), task_id, context.teacher_id),
                )
            else:
                connection.execute(
                    """
                    UPDATE analysis_tasks SET query = ?, status = ?, report_id = NULL,
                        clarification_json = NULL, execution_json = NULL,
                        error_code = NULL, error_message = NULL,
                        updated_at = ? WHERE task_id = ? AND teacher_id = ?
                    """,
                    (
                        query,
                        TaskStatus.RUNNING.value,
                        utc_iso(),
                        task_id,
                        context.teacher_id,
                    ),
                )
        self.audit(context, "task_restarted", "task", task_id)

    def update_task_result(
        self,
        task_id: str,
        context: TeacherExecutionContext,
        *,
        status: TaskStatus,
        report: LearningReport | None = None,
        validation: ReportValidationResult | None = None,
        execution: dict[str, Any] | None = None,
        clarification: dict[str, Any] | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> None:
        self.get_task_row(task_id, context.teacher_id)
        now = utc_iso()
        with self._connection() as connection:
            connection.execute(
                """
                UPDATE analysis_tasks
                SET status = ?, report_id = ?, clarification_json = ?, execution_json = ?,
                    error_code = ?, error_message = ?, updated_at = ?
                WHERE task_id = ? AND teacher_id = ?
                """,
                (
                    status.value,
                    report.report_id if report else None,
                    json.dumps(clarification, ensure_ascii=False) if clarification else None,
                    json.dumps(execution, ensure_ascii=False) if execution else None,
                    error_code,
                    error_message,
                    now,
                    task_id,
                    context.teacher_id,
                ),
            )
            if report and validation:
                connection.execute(
                    """
                    INSERT INTO learning_reports (
                        report_id, task_id, teacher_id, report_json, validation_json,
                        confirmation_status, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(task_id) DO UPDATE SET
                        report_id = excluded.report_id,
                        report_json = excluded.report_json,
                        validation_json = excluded.validation_json,
                        confirmation_status = excluded.confirmation_status,
                        confirmed_by = NULL,
                        confirmed_at = NULL,
                        updated_at = excluded.updated_at
                    """,
                    (
                        report.report_id,
                        task_id,
                        context.teacher_id,
                        report.model_dump_json(),
                        validation.model_dump_json(),
                        ConfirmationStatus.UNCONFIRMED.value,
                        now,
                        now,
                    ),
                )
        self.audit(
            context,
            "task_finished",
            "task",
            task_id,
            {"status": status.value, "report_id": report.report_id if report else None},
        )

    def get_task_execution(self, task_id: str, teacher_id: str) -> dict[str, Any] | None:
        row = self.get_task_row(task_id, teacher_id)
        return json.loads(row["execution_json"]) if row["execution_json"] else None

    def get_report(self, report_id: str, teacher_id: str) -> ReportRecord:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM learning_reports WHERE report_id = ? AND teacher_id = ?",
                (report_id, teacher_id),
            ).fetchone()
            exists = connection.execute(
                "SELECT 1 FROM learning_reports WHERE report_id = ?", (report_id,)
            ).fetchone()
        if row is None:
            if exists:
                raise PermissionDeniedError("无权访问该报告")
            raise StudentNotFoundError("报告不存在")
        return ReportRecord(
            report=LearningReport.model_validate_json(row["report_json"]),
            validation=ReportValidationResult.model_validate_json(row["validation_json"]),
            confirmation_status=row["confirmation_status"],
            confirmed_by=row["confirmed_by"],
            confirmed_at=(
                datetime.fromisoformat(row["confirmed_at"]) if row["confirmed_at"] else None
            ),
            teacher_edits=json.loads(row["teacher_edits_json"]),
        )

    def report_task_id(self, report_id: str, teacher_id: str) -> str:
        self.get_report(report_id, teacher_id)
        with self._connection() as connection:
            row = connection.execute(
                "SELECT task_id FROM learning_reports WHERE report_id = ? AND teacher_id = ?",
                (report_id, teacher_id),
            ).fetchone()
        return str(row["task_id"])

    def save_report_edit(
        self,
        report: LearningReport,
        validation: ReportValidationResult,
        context: TeacherExecutionContext,
    ) -> None:
        self.get_report(report.report_id, context.teacher_id)
        with self._connection() as connection:
            connection.execute(
                """
                UPDATE learning_reports
                SET report_json = ?, validation_json = ?, teacher_edits_json = ?,
                    confirmation_status = ?, confirmed_by = NULL, confirmed_at = NULL,
                    updated_at = ?
                WHERE report_id = ? AND teacher_id = ?
                """,
                (
                    report.model_dump_json(),
                    validation.model_dump_json(),
                    json.dumps({"edited": True}, ensure_ascii=False),
                    ConfirmationStatus.UNCONFIRMED.value,
                    utc_iso(),
                    report.report_id,
                    context.teacher_id,
                ),
            )
        self.audit(context, "report_edited", "report", report.report_id)

    def confirm_report(
        self,
        report_id: str,
        context: TeacherExecutionContext,
        *,
        has_comments: bool = False,
    ) -> None:
        record = self.get_report(report_id, context.teacher_id)
        if not record.validation.passed:
            raise ValueError("事实校验未通过，不能确认报告")
        task_id = self.report_task_id(report_id, context.teacher_id)
        now = utc_iso()
        with self._connection() as connection:
            connection.execute(
                """
                UPDATE learning_reports SET confirmation_status = ?, confirmed_by = ?,
                    confirmed_at = ?, updated_at = ?
                WHERE report_id = ? AND teacher_id = ?
                """,
                (
                    ConfirmationStatus.CONFIRMED.value,
                    context.teacher_id,
                    now,
                    now,
                    report_id,
                    context.teacher_id,
                ),
            )
            connection.execute(
                "UPDATE analysis_tasks SET status = ?, updated_at = ? WHERE task_id = ?",
                (TaskStatus.COMPLETED.value, now, task_id),
            )
        self.audit(
            context,
            "report_confirmed",
            "report",
            report_id,
            {"has_comments": has_comments},
        )
