from datetime import date
from pathlib import Path

from futureedu_insight.adapters import SQLiteLearningDataGateway
from futureedu_insight.domain.models import (
    DateRange,
    LearningDataBundle,
    TeacherExecutionContext,
)
from futureedu_insight.rag.case_store import JsonCaseStore
from futureedu_insight.rag.hybrid_retriever import HybridCaseRetriever, build_case_query
from futureedu_insight.tools import build_learning_profile, calculate_learning_metrics

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _profile(
    gateway: SQLiteLearningDataGateway,
    context: TeacherExecutionContext,
):
    period = DateRange(start=date(2026, 3, 1), end=date(2026, 4, 30))
    student = gateway.get_student_profile("S1001", context)
    bundle = LearningDataBundle(
        student=student,
        scores=gateway.get_score_records("S1001", "数学", period.start, period.end, context),
        homework=gateway.get_homework_records("S1001", "数学", period.start, period.end, context),
        attendance=gateway.get_attendance_records("S1001", period.start, period.end, context),
        feedback=gateway.get_classroom_feedback("S1001", "数学", period.start, period.end, context),
    )
    return build_learning_profile(
        student,
        "数学",
        period,
        calculate_learning_metrics(bundle),
        rules_version="1.0.0",
    )


def test_query_is_built_only_from_structured_profile(
    gateway: SQLiteLearningDataGateway,
    teacher_context: TeacherExecutionContext,
) -> None:
    query = build_case_query(_profile(gateway, teacher_context))
    assert "八年级" in query
    assert "数学" in query
    assert "一次函数应用题" in query
    assert "审题过快" in query


def test_hybrid_retrieval_filters_metadata_and_ranks_relevant_case_first(
    gateway: SQLiteLearningDataGateway,
    teacher_context: TeacherExecutionContext,
) -> None:
    store = JsonCaseStore(PROJECT_ROOT / "data" / "cases")
    retriever = HybridCaseRetriever(store, min_relevance=0.2, final_count=3)
    profile = _profile(gateway, teacher_context)

    results = retriever.retrieve(profile)

    assert results
    assert results[0].case.case_id == "CASE-MATH-0001"
    assert all(result.case.subject == "数学" for result in results)
    assert all(result.case.grade == "八年级" for result in results)
    assert all(result.case.score_trend == profile.score_trend for result in results)
    assert all(0 <= result.relevance_score <= 1 for result in results)
    assert all(result.citation_text for result in results)


def test_retrieval_returns_empty_when_no_metadata_candidate(
    gateway: SQLiteLearningDataGateway,
    teacher_context: TeacherExecutionContext,
) -> None:
    profile = _profile(gateway, teacher_context).model_copy(update={"subject": "语文"})
    store = JsonCaseStore(PROJECT_ROOT / "data" / "cases")
    retriever = HybridCaseRetriever(store)

    assert retriever.retrieve(profile) == []
