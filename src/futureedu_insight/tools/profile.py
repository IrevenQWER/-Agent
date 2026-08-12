from __future__ import annotations

from futureedu_insight.domain.models import (
    DateRange,
    LearningMetrics,
    LearningProfile,
    ScoreTrend,
    StudentProfile,
)


def build_learning_profile(
    student: StudentProfile,
    subject: str,
    period: DateRange,
    metrics: LearningMetrics,
    *,
    rules_version: str,
) -> LearningProfile:
    """Map deterministic metrics to versioned learning tags."""

    tags: list[str] = []
    if metrics.score_trend == ScoreTrend.DECLINING:
        tags.append("declining_score")
    elif metrics.score_trend == ScoreTrend.IMPROVING:
        tags.append("improving_score")
    elif metrics.score_trend == ScoreTrend.STABLE:
        tags.append("stable_score")
    else:
        tags.append("insufficient_score_history")

    if metrics.homework_submission_rate is not None:
        if metrics.homework_submission_rate < 0.8:
            tags.append("low_homework_submission")
        elif metrics.homework_submission_rate >= 0.95:
            tags.append("high_homework_submission")

    if metrics.correction_rate is not None and metrics.correction_rate < 0.5:
        tags.append("low_correction_rate")
    if metrics.attendance_rate is not None and metrics.attendance_rate < 0.9:
        tags.append("attendance_risk")
    if metrics.weak_knowledge_points:
        tags.append("knowledge_weakness")
    if "审题过快" in metrics.classroom_tags:
        tags.append("rushed_question_reading")
    if "过程书写不完整" in metrics.classroom_tags:
        tags.append("incomplete_working")

    return LearningProfile(
        student_id=student.student_id,
        grade=student.grade,
        subject=subject,
        period=period,
        rules_version=rules_version,
        score_trend=metrics.score_trend,
        score_delta=metrics.score_delta,
        rank_percentile_change=metrics.rank_percentile_change,
        homework_submission_rate=metrics.homework_submission_rate,
        homework_accuracy_rate=metrics.homework_accuracy_rate,
        correction_rate=metrics.correction_rate,
        attendance_rate=metrics.attendance_rate,
        weak_knowledge_points=metrics.weak_knowledge_points,
        classroom_tags=metrics.classroom_tags,
        learning_tags=tags,
        data_completeness=metrics.data_completeness,
        evidence_record_ids=metrics.evidence_record_ids,
    )
