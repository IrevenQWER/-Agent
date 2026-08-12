from __future__ import annotations

import re

from futureedu_insight.domain.models import (
    LearningDataBundle,
    LearningMetrics,
    LearningProfile,
    LearningReport,
    ReportValidationResult,
    RetrievedCase,
    ScoreTrend,
    ValidationIssue,
)

BANNED_LABELS = ("天赋差", "态度差", "笨", "懒惰", "没救")
PRIVACY_PATTERNS = (
    re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)"),
    re.compile(r"(?<![0-9Xx])\d{17}[0-9Xx](?![0-9Xx])"),
)


def _issue(code: str, field: str, message: str, expected=None, actual=None) -> ValidationIssue:
    return ValidationIssue(
        code=code,
        field=field,
        message=message,
        expected=expected,
        actual=actual,
    )


def _report_text(report: LearningReport) -> str:
    values = [
        report.overall_summary,
        report.score_analysis,
        report.homework_analysis,
        report.attendance_analysis,
        report.classroom_analysis,
        report.parent_communication_summary,
    ]
    values.extend(item.conclusion for item in report.strengths + report.risks)
    values.extend(item.action + item.reason for item in report.recommended_actions)
    return "\n".join(value for value in values if value)


def validate_report(
    report: LearningReport,
    data: LearningDataBundle,
    metrics: LearningMetrics,
    profile: LearningProfile,
    retrieved_cases: list[RetrievedCase],
    *,
    parent_summary_requested: bool,
) -> ReportValidationResult:
    """Validate generated facts and citations against deterministic source data."""

    numeric_errors: list[ValidationIssue] = []
    trend_errors: list[ValidationIssue] = []
    citation_errors: list[ValidationIssue] = []
    privacy_errors: list[ValidationIssue] = []
    unsupported_claims: list[ValidationIssue] = []

    exact_fields = (
        ("student_id", data.student.student_id, report.student_id),
        ("subject", profile.subject, report.subject),
        ("period", profile.period, report.period),
        ("data_completeness", metrics.data_completeness, report.data_completeness),
        (
            "weak_knowledge_points",
            metrics.weak_knowledge_points,
            report.weak_knowledge_points,
        ),
    )
    for field, expected, actual in exact_fields:
        if actual != expected:
            numeric_errors.append(
                _issue("FACT_MISMATCH", field, "报告字段与事实源不一致", expected, actual)
            )

    trend_text = {
        ScoreTrend.DECLINING: "呈下降趋势",
        ScoreTrend.IMPROVING: "呈上升趋势",
        ScoreTrend.STABLE: "总体稳定",
        ScoreTrend.INSUFFICIENT: "数据不足，暂不能判断趋势",
    }[metrics.score_trend]
    if data.scores and (not report.score_analysis or trend_text not in report.score_analysis):
        trend_errors.append(
            _issue("TREND_MISMATCH", "score_analysis", "成绩趋势与计算结果不一致", trend_text)
        )

    numeric_expectations: list[tuple[str, str | None, str]] = []
    if data.scores:
        numeric_expectations.extend(
            (
                ("score_analysis", f"共有{len(data.scores)}次成绩记录", "成绩记录数"),
                (
                    "score_analysis",
                    (
                        "暂无可计算数据"
                        if metrics.score_delta is None
                        else f"{metrics.score_delta}个百分点"
                    ),
                    "首末次成绩变化",
                ),
                (
                    "score_analysis",
                    (
                        "暂无可计算数据"
                        if metrics.class_average_gap is None
                        else f"{metrics.class_average_gap}个百分点"
                    ),
                    "班级均分差",
                ),
            )
        )
    if data.homework:
        for value, label in (
            (metrics.homework_submission_rate, "作业提交率"),
            (metrics.homework_accuracy_rate, "作业正确率"),
            (metrics.correction_rate, "错题订正率"),
        ):
            numeric_expectations.append(
                (
                    "homework_analysis",
                    "暂无可计算数据" if value is None else f"{value:.0%}",
                    label,
                )
            )
    if data.attendance:
        numeric_expectations.append(
            (
                "attendance_analysis",
                (
                    "暂无可计算数据"
                    if metrics.attendance_rate is None
                    else f"{metrics.attendance_rate:.0%}"
                ),
                "到课率",
            )
        )
    for field, expected_text, label in numeric_expectations:
        actual_text = getattr(report, field)
        if expected_text and (not actual_text or expected_text not in actual_text):
            numeric_errors.append(
                _issue(
                    "NUMERIC_TEXT_MISMATCH",
                    field,
                    f"{label}与计算结果不一致",
                    expected_text,
                    actual_text,
                )
            )

    business_ids = set(metrics.evidence_record_ids)
    case_ids = {item.case.case_id for item in retrieved_cases}
    allowed_ids = business_ids | case_ids
    used_ids = {item.evidence_id for item in report.evidence}
    used_ids.update(
        evidence_id
        for conclusion in report.strengths + report.risks
        for evidence_id in conclusion.evidence_ids
    )
    for action in report.recommended_actions:
        used_ids.update(action.evidence_ids)
        used_ids.update(action.reference_case_ids)
    unknown_ids = sorted(used_ids - allowed_ids)
    if unknown_ids:
        citation_errors.append(
            _issue("UNKNOWN_EVIDENCE", "evidence", "引用了不存在的证据", [], unknown_ids)
        )
    declared_ids = {item.evidence_id for item in report.evidence}
    referenced_ids = {
        evidence_id
        for conclusion in report.strengths + report.risks
        for evidence_id in conclusion.evidence_ids
    }
    for action in report.recommended_actions:
        referenced_ids.update(action.evidence_ids)
        referenced_ids.update(action.reference_case_ids)
    undeclared_ids = sorted(referenced_ids - declared_ids)
    if undeclared_ids:
        citation_errors.append(
            _issue(
                "UNDECLARED_EVIDENCE",
                "evidence",
                "结论或建议引用的证据未列入证据清单",
                [],
                undeclared_ids,
            )
        )
    if set(report.retrieved_case_ids) - case_ids:
        citation_errors.append(
            _issue(
                "UNKNOWN_CASE",
                "retrieved_case_ids",
                "报告引用了未检索到的案例",
                sorted(case_ids),
                report.retrieved_case_ids,
            )
        )

    case_by_id = {item.case.case_id: item.case for item in retrieved_cases}
    for index, action in enumerate(report.recommended_actions):
        referenced = [
            case_by_id[case_id] for case_id in action.reference_case_ids if case_id in case_by_id
        ]
        if referenced and not any(
            action.action == case.intervention
            and action.duration == case.observation_period
            and action.observation_metric == case.observed_metric
            for case in referenced
        ):
            citation_errors.append(
                _issue(
                    "UNSUPPORTED_CASE_ACTION",
                    f"recommended_actions.{index}",
                    "干预建议、周期或观察指标超出所引案例支持范围",
                )
            )
        expected_level = (
            "high"
            if action.confidence_score >= 0.8
            else "medium"
            if action.confidence_score >= 0.55
            else "low"
        )
        if action.confidence_level.value != expected_level:
            numeric_errors.append(
                _issue(
                    "CONFIDENCE_LEVEL_MISMATCH",
                    f"recommended_actions.{index}.confidence_level",
                    "置信等级与置信分数不一致",
                    expected_level,
                    action.confidence_level.value,
                )
            )

    if not parent_summary_requested and report.parent_communication_summary is not None:
        unsupported_claims.append(
            _issue(
                "UNREQUESTED_PARENT_SUMMARY",
                "parent_communication_summary",
                "用户未请求家校沟通摘要",
            )
        )

    text = _report_text(report)
    unsupported_trend_word = next(
        (
            phrase
            for phrase in ("连续下降", "连续上升", "持续下降", "持续上升", "持续走低", "持续走高")
            if phrase in text
        ),
        None,
    )
    if len(data.scores) < 3 and unsupported_trend_word:
        unsupported_claims.append(
            _issue(
                "INSUFFICIENT_CONTINUOUS_TREND",
                "report",
                f"少于三个成绩时间点时不能描述为{unsupported_trend_word}",
            )
        )
    for label in BANNED_LABELS:
        if label in text:
            unsupported_claims.append(
                _issue("HARMFUL_LABEL", "report", f"包含不当学生标签：{label}")
            )
    for pattern in PRIVACY_PATTERNS:
        match = pattern.search(text)
        if match:
            privacy_errors.append(
                _issue("PII_LEAK", "report", "报告包含疑似敏感个人信息", actual=match.group())
            )

    errors_exist = any(
        (numeric_errors, trend_errors, citation_errors, privacy_errors, unsupported_claims)
    )
    return ReportValidationResult(
        passed=not errors_exist,
        numeric_errors=numeric_errors,
        trend_errors=trend_errors,
        citation_errors=citation_errors,
        privacy_errors=privacy_errors,
        unsupported_claims=unsupported_claims,
        retryable=bool(numeric_errors or trend_errors or citation_errors),
    )
