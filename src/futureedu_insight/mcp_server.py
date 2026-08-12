from __future__ import annotations

import os
import sqlite3
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from futureedu_insight.adapters.sqlite_learning_data import SQLiteLearningDataGateway
from futureedu_insight.config import get_settings
from futureedu_insight.domain.errors import PermissionDeniedError, StudentNotFoundError
from futureedu_insight.domain.models import TeacherExecutionContext, ToolResult


def _bound_context() -> TeacherExecutionContext:
    teacher_id = os.getenv("MCP_TRUSTED_TEACHER_ID")
    if not teacher_id:
        raise RuntimeError("MCP_TRUSTED_TEACHER_ID is required")
    return TeacherExecutionContext(
        teacher_id=teacher_id,
        request_id=os.getenv("MCP_TRUSTED_REQUEST_ID", "MCP-REQUEST"),
        session_id=os.getenv("MCP_TRUSTED_SESSION_ID", "MCP-SESSION"),
    )


def create_server(database_path: Path | str | None = None) -> FastMCP:
    """Build a Learning Data MCP server bound to a trusted teacher context."""

    configured_path = database_path or os.getenv("MCP_DATABASE_PATH")
    gateway = SQLiteLearningDataGateway(configured_path or get_settings().database_path)
    context = _bound_context()
    server = FastMCP(
        "futureedu-learning-data",
        instructions="只返回当前受信任教师授权范围内的最小化学情数据。",
        log_level="ERROR",
    )

    def execute(call: Callable[[], Any]) -> dict[str, Any]:
        try:
            data = call()
            items = data if isinstance(data, list) else [data]
            source_ids = [item.record_id for item in items if hasattr(item, "record_id")]
            payload = (
                [item.model_dump(mode="json") for item in data]
                if isinstance(data, list)
                else data.model_dump(mode="json")
            )
            return ToolResult[Any](
                success=True,
                data=payload,
                source_record_ids=source_ids,
            ).model_dump(mode="json")
        except PermissionDeniedError as exc:
            return ToolResult[Any](
                success=False,
                error_code="PERMISSION_DENIED",
                error_message=str(exc),
            ).model_dump(mode="json")
        except StudentNotFoundError as exc:
            return ToolResult[Any](
                success=False,
                error_code="STUDENT_NOT_FOUND",
                error_message=str(exc),
            ).model_dump(mode="json")
        except sqlite3.Error as exc:
            return ToolResult[Any](
                success=False,
                error_code="DATA_SOURCE_ERROR",
                error_message=f"学情数据源查询失败: {type(exc).__name__}",
            ).model_dump(mode="json")

    @server.tool(structured_output=True)
    def resolve_students(identifier: str, class_context: str | None = None) -> dict[str, Any]:
        """在当前教师授权名册内解析学生编号或姓名。"""

        return execute(lambda: gateway.resolve_students(identifier, class_context, context))

    @server.tool(structured_output=True)
    def get_student_profile(student_id: str) -> dict[str, Any]:
        """读取当前教师有权访问的脱敏学生档案。"""

        return execute(lambda: gateway.get_student_profile(student_id, context))

    @server.tool(structured_output=True)
    def get_score_records(
        student_id: str, subject: str, start_date: date, end_date: date
    ) -> dict[str, Any]:
        """读取指定学生、学科和时间范围的成绩记录。"""

        return execute(
            lambda: gateway.get_score_records(student_id, subject, start_date, end_date, context)
        )

    @server.tool(structured_output=True)
    def get_homework_records(
        student_id: str, subject: str, start_date: date, end_date: date
    ) -> dict[str, Any]:
        """读取指定学生、学科和时间范围的作业记录。"""

        return execute(
            lambda: gateway.get_homework_records(student_id, subject, start_date, end_date, context)
        )

    @server.tool(structured_output=True)
    def get_attendance_records(student_id: str, start_date: date, end_date: date) -> dict[str, Any]:
        """读取指定学生和时间范围的考勤记录。"""

        return execute(
            lambda: gateway.get_attendance_records(student_id, start_date, end_date, context)
        )

    @server.tool(structured_output=True)
    def get_classroom_feedback(
        student_id: str, subject: str, start_date: date, end_date: date
    ) -> dict[str, Any]:
        """读取指定学生、学科和时间范围的课堂反馈。"""

        return execute(
            lambda: gateway.get_classroom_feedback(
                student_id, subject, start_date, end_date, context
            )
        )

    return server


def main() -> None:
    create_server().run(transport="stdio")


if __name__ == "__main__":
    main()
