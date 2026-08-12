import sqlite3
from pathlib import Path

from futureedu_insight.agent import LearningInsightAgent
from futureedu_insight.agent.report_generator import DeterministicReportGenerator
from futureedu_insight.domain.models import TaskStatus, TeacherExecutionContext
from futureedu_insight.rag.case_store import JsonCaseStore
from futureedu_insight.rag.hybrid_retriever import HybridCaseRetriever

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _agent(gateway) -> LearningInsightAgent:
    return LearningInsightAgent(
        gateway,
        HybridCaseRetriever(JsonCaseStore(PROJECT_ROOT / "data" / "cases"), min_relevance=0.2),
        generator=DeterministicReportGenerator(prompt_version="1.0.0"),
    )


def test_graph_runs_end_to_end_and_stops_for_teacher_confirmation(gateway, teacher_context) -> None:
    state = _agent(gateway).analyze(
        "生成学生 S1001 2026年3月数学学情报告，重点看成绩和作业",
        teacher_context,
    )

    assert state["status"] == TaskStatus.AWAITING_CONFIRMATION
    assert state["report"].student_id == "S1001"
    assert state["validation"].passed is True
    assert state["generation_attempt"] == 1


def test_graph_requests_missing_period_instead_of_guessing(gateway, teacher_context) -> None:
    state = _agent(gateway).analyze("分析学生 S1001 的数学学情", teacher_context)

    assert state["status"] == TaskStatus.NEEDS_CLARIFICATION
    assert state["clarification"]["missing_fields"] == ["period"]
    assert "report" not in state


def test_graph_does_not_disclose_student_outside_teacher_scope(gateway) -> None:
    context = TeacherExecutionContext(
        teacher_id="T1001", request_id="REQ-DENIED", session_id="SESSION-DENIED"
    )

    state = _agent(gateway).analyze("生成学生 S2001 2026年3月数学学情报告", context)

    assert state["status"] == TaskStatus.FAILED
    assert state["error_code"] == "STUDENT_NOT_FOUND"
    assert "当前教师可访问范围" in state["error_message"]


def test_graph_reports_insufficient_score_data(gateway, teacher_context) -> None:
    state = _agent(gateway).analyze("生成学生 S1001 2026年5月数学学情报告", teacher_context)

    assert state["status"] == TaskStatus.INSUFFICIENT_DATA
    assert state["error_code"] == "INSUFFICIENT_SCORE_DATA"


def test_graph_returns_candidates_for_ambiguous_student_name(
    database_path, gateway, teacher_context
) -> None:
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "INSERT INTO teacher_student_access (teacher_id, student_id) VALUES (?, ?)",
            ("T1001", "S2001"),
        )

    state = _agent(gateway).analyze("生成学生张晨2026年3月数学学情报告", teacher_context)

    assert state["status"] == TaskStatus.NEEDS_CLARIFICATION
    assert len(state["clarification"]["candidates"]) == 2
