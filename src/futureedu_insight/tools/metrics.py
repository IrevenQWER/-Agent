from __future__ import annotations

from collections import Counter, defaultdict

from futureedu_insight.domain.models import (
    LearningDataBundle,
    LearningMetrics,
    ScoreTrend,
)


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _linear_slope(values: list[float]) -> float | None:
    if len(values) < 2:
        return None
    x_mean = (len(values) - 1) / 2
    y_mean = sum(values) / len(values)
    numerator = sum((index - x_mean) * (value - y_mean) for index, value in enumerate(values))
    denominator = sum((index - x_mean) ** 2 for index in range(len(values)))
    return numerator / denominator if denominator else 0.0


def _score_trend(slope: float | None, sample_count: int) -> ScoreTrend:
    if slope is None:
        return ScoreTrend.INSUFFICIENT
    if slope < -0.02:
        return ScoreTrend.DECLINING
    if slope > 0.02:
        return ScoreTrend.IMPROVING
    if sample_count < 3:
        return ScoreTrend.INSUFFICIENT
    return ScoreTrend.STABLE


def calculate_learning_metrics(data: LearningDataBundle) -> LearningMetrics:
    """Calculate reproducible metrics without invoking an LLM."""

    scores = sorted(data.scores, key=lambda item: (item.exam_date, item.record_id))
    normalized_scores = [record.score / record.full_score for record in scores]
    slope = _linear_slope(normalized_scores)
    score_delta = None
    class_average_gap = None
    rank_percentile_change = None

    if len(scores) >= 2:
        score_delta = round((normalized_scores[-1] - normalized_scores[0]) * 100, 2)
        first_percentile = 1 - scores[0].rank / scores[0].participant_count
        last_percentile = 1 - scores[-1].rank / scores[-1].participant_count
        rank_percentile_change = round(last_percentile - first_percentile, 4)
    if scores:
        latest = scores[-1]
        class_average_gap = round(
            (latest.score - latest.class_average) / latest.full_score * 100, 2
        )

    homework_count = len(data.homework)
    submitted = [item for item in data.homework if item.submitted]
    accuracy_values = [item.accuracy_rate for item in submitted if item.accuracy_rate is not None]
    correction_values = [item.corrected for item in submitted if item.corrected is not None]

    homework_submission_rate = len(submitted) / homework_count if homework_count else None
    homework_accuracy_rate = _mean(accuracy_values)
    correction_rate = (
        sum(bool(value) for value in correction_values) / len(correction_values)
        if correction_values
        else None
    )

    attendance_rate = None
    if data.attendance:
        attended = sum(item.status in {"present", "late"} for item in data.attendance)
        attendance_rate = attended / len(data.attendance)

    knowledge_rates: dict[str, list[float]] = defaultdict(list)
    for record in scores:
        for knowledge_point, rate in record.knowledge_scores.items():
            knowledge_rates[knowledge_point].append(rate)
    weak_knowledge_points = sorted(
        (
            point
            for point, rates in knowledge_rates.items()
            if rates and sum(rates) / len(rates) < 0.7
        ),
        key=lambda point: (sum(knowledge_rates[point]) / len(knowledge_rates[point]), point),
    )

    tag_counter: Counter[str] = Counter()
    for feedback in data.feedback:
        tag_counter.update(feedback.performance_tags)
    classroom_tags = [tag for tag, _ in tag_counter.most_common()]

    completeness = 0.0
    completeness += 0.35 if scores else 0
    completeness += 0.25 if data.homework else 0
    completeness += 0.15 if data.attendance else 0
    completeness += 0.25 if data.feedback else 0

    source_ids = [item.record_id for item in scores]
    source_ids.extend(item.record_id for item in data.homework)
    source_ids.extend(item.record_id for item in data.attendance)
    source_ids.extend(item.record_id for item in data.feedback)

    return LearningMetrics(
        normalized_scores=[round(value, 4) for value in normalized_scores],
        score_delta=score_delta,
        score_slope=None if slope is None else round(slope, 4),
        score_trend=_score_trend(slope, len(scores)),
        class_average_gap=class_average_gap,
        rank_percentile_change=rank_percentile_change,
        homework_submission_rate=(
            None if homework_submission_rate is None else round(homework_submission_rate, 4)
        ),
        homework_accuracy_rate=(
            None if homework_accuracy_rate is None else round(homework_accuracy_rate, 4)
        ),
        correction_rate=None if correction_rate is None else round(correction_rate, 4),
        attendance_rate=None if attendance_rate is None else round(attendance_rate, 4),
        weak_knowledge_points=weak_knowledge_points,
        classroom_tags=classroom_tags,
        data_completeness=round(completeness, 2),
        evidence_record_ids=source_ids,
    )
