from datetime import date

import pytest

from futureedu_insight.adapters import SQLiteLearningDataGateway
from futureedu_insight.domain.models import (
    DateRange,
    LearningDataBundle,
    ScoreTrend,
    TeacherExecutionContext,
)
from futureedu_insight.tools import build_learning_profile, calculate_learning_metrics


def _bundle(
    gateway: SQLiteLearningDataGateway,
    context: TeacherExecutionContext,
) -> tuple[LearningDataBundle, DateRange]:
    period = DateRange(start=date(2026, 3, 1), end=date(2026, 4, 30))
    student = gateway.get_student_profile("S1001", context)
    scores = gateway.get_score_records("S1001", "数学", period.start, period.end, context)
    homework = gateway.get_homework_records("S1001", "数学", period.start, period.end, context)
    attendance = gateway.get_attendance_records("S1001", period.start, period.end, context)
    feedback = gateway.get_classroom_feedback("S1001", "数学", period.start, period.end, context)
    return (
        LearningDataBundle(
            student=student,
            scores=scores,
            homework=homework,
            attendance=attendance,
            feedback=feedback,
        ),
        period,
    )


def test_metric_calculation_is_deterministic_and_correct(
    gateway: SQLiteLearningDataGateway,
    teacher_context: TeacherExecutionContext,
) -> None:
    bundle, _ = _bundle(gateway, teacher_context)
    first = calculate_learning_metrics(bundle)
    second = calculate_learning_metrics(bundle)

    assert first == second
    assert first.normalized_scores == [0.89, 0.85, 0.81]
    assert first.score_delta == -8.0
    assert first.score_slope == -0.04
    assert first.score_trend == ScoreTrend.DECLINING
    assert first.class_average_gap == -2.0
    assert first.rank_percentile_change == pytest.approx(-0.2857)
    assert first.homework_submission_rate == 0.8
    assert first.homework_accuracy_rate == pytest.approx(0.7625)
    assert first.correction_rate == 0.25
    assert first.attendance_rate == 1.0
    assert first.weak_knowledge_points == ["一次函数应用题", "几何辅助线"]
    assert first.data_completeness == 1.0


def test_profile_uses_only_versioned_rules(
    gateway: SQLiteLearningDataGateway,
    teacher_context: TeacherExecutionContext,
) -> None:
    bundle, period = _bundle(gateway, teacher_context)
    metrics = calculate_learning_metrics(bundle)
    profile = build_learning_profile(
        bundle.student,
        "数学",
        period,
        metrics,
        rules_version="1.0.0",
    )

    assert profile.rules_version == "1.0.0"
    assert profile.learning_tags == [
        "declining_score",
        "low_correction_rate",
        "knowledge_weakness",
        "rushed_question_reading",
        "incomplete_working",
    ]
    assert profile.evidence_record_ids == metrics.evidence_record_ids
