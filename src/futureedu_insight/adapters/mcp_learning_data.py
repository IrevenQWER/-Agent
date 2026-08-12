from __future__ import annotations

import asyncio
import os
import sys
from datetime import date
from pathlib import Path
from typing import Any, TypeVar

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from pydantic import TypeAdapter

from futureedu_insight.domain.errors import PermissionDeniedError, StudentNotFoundError
from futureedu_insight.domain.models import (
    AttendanceRecord,
    ClassroomFeedback,
    HomeworkRecord,
    ScoreRecord,
    StudentProfile,
    TeacherExecutionContext,
    ToolResult,
)

ModelT = TypeVar("ModelT")


class McpLearningDataAdapter:
    """stdio MCP client adapter for a single trusted teacher execution context."""

    def __init__(
        self,
        database_path: Path | str,
        context: TeacherExecutionContext,
        *,
        python_executable: str | None = None,
    ) -> None:
        self.database_path = Path(database_path)
        self.bound_context = context
        self.python_executable = python_executable or sys.executable

    def _assert_context(self, context: TeacherExecutionContext) -> None:
        if context != self.bound_context:
            raise PermissionDeniedError("MCP 连接上下文与当前请求不匹配")

    async def _call_async(self, name: str, arguments: dict[str, Any]) -> ToolResult[Any]:
        environment = os.environ.copy()
        environment.update(
            {
                "MCP_DATABASE_PATH": str(self.database_path),
                "MCP_TRUSTED_TEACHER_ID": self.bound_context.teacher_id,
                "MCP_TRUSTED_REQUEST_ID": self.bound_context.request_id,
                "MCP_TRUSTED_SESSION_ID": self.bound_context.session_id,
            }
        )
        parameters = StdioServerParameters(
            command=self.python_executable,
            args=["-m", "futureedu_insight.mcp_server"],
            env=environment,
        )
        async with (
            stdio_client(parameters) as (read, write),
            ClientSession(read, write) as session,
        ):
            await session.initialize()
            result = await session.call_tool(name, arguments)
        if result.isError:
            message = next(
                (item.text for item in result.content if hasattr(item, "text")),
                "MCP tool call failed",
            )
            raise RuntimeError(message)
        payload = result.structuredContent
        if payload and "result" in payload and len(payload) == 1:
            payload = payload["result"]
        return ToolResult[Any].model_validate(payload)

    def _call(
        self,
        name: str,
        arguments: dict[str, Any],
        context: TeacherExecutionContext,
        response_type: Any,
    ):
        self._assert_context(context)
        result = asyncio.run(self._call_async(name, arguments))
        if not result.success:
            if result.error_code == "PERMISSION_DENIED":
                raise PermissionDeniedError(result.error_message or "无权访问")
            if result.error_code == "STUDENT_NOT_FOUND":
                raise StudentNotFoundError(result.error_message or "未找到学生")
            raise RuntimeError(result.error_message or result.error_code)
        return TypeAdapter(response_type).validate_python(result.data)

    def resolve_students(
        self,
        identifier: str,
        class_context: str | None,
        context: TeacherExecutionContext,
    ) -> list[StudentProfile]:
        return self._call(
            "resolve_students",
            {"identifier": identifier, "class_context": class_context},
            context,
            list[StudentProfile],
        )

    def get_student_profile(
        self, student_id: str, context: TeacherExecutionContext
    ) -> StudentProfile:
        return self._call(
            "get_student_profile", {"student_id": student_id}, context, StudentProfile
        )

    def get_score_records(
        self,
        student_id: str,
        subject: str,
        start_date: date,
        end_date: date,
        context: TeacherExecutionContext,
    ) -> list[ScoreRecord]:
        return self._call(
            "get_score_records",
            {
                "student_id": student_id,
                "subject": subject,
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
            },
            context,
            list[ScoreRecord],
        )

    def get_homework_records(
        self,
        student_id: str,
        subject: str,
        start_date: date,
        end_date: date,
        context: TeacherExecutionContext,
    ) -> list[HomeworkRecord]:
        return self._call(
            "get_homework_records",
            {
                "student_id": student_id,
                "subject": subject,
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
            },
            context,
            list[HomeworkRecord],
        )

    def get_attendance_records(
        self,
        student_id: str,
        start_date: date,
        end_date: date,
        context: TeacherExecutionContext,
    ) -> list[AttendanceRecord]:
        return self._call(
            "get_attendance_records",
            {
                "student_id": student_id,
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
            },
            context,
            list[AttendanceRecord],
        )

    def get_classroom_feedback(
        self,
        student_id: str,
        subject: str,
        start_date: date,
        end_date: date,
        context: TeacherExecutionContext,
    ) -> list[ClassroomFeedback]:
        return self._call(
            "get_classroom_feedback",
            {
                "student_id": student_id,
                "subject": subject,
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
            },
            context,
            list[ClassroomFeedback],
        )
