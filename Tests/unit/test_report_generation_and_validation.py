from datetime import date
from pathlib import Path

from futureedu_insight.agent.report_generator import DeterministicReportGenerator
from futureedu_insight.domain.models import DateRange, LearningDataBundle
from futureedu_insight.rag.case_store import JsonCaseStore
from futureedu_insight.rag.hybrid_retriever import HybridCaseRetriever
from futureedu_insight.tools import (
    build_learning_profile,
    calculate_learning_metrics,
    validate_report,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _analysis_inputs(gateway, teacher_context):
    period = DateRange(start=date(2026, 3, 1), end=date(2026, 4, 30))
    student = gateway.get_student_profile("S1001", teacher_context)
    data = LearningDataBundle(
        student=student,
        scores=gateway.get_score_records(
            "S1001", "数学", period.start, period.end, teacher_context
        ),
        homework=gateway.get_homework_records(
            "S1001", "数学", period.start, period.end, teacher_context
        ),
        attendance=gateway.get_attendance_records(
            "S1001", period.start, period.end, teacher_context
        ),
        feedback=gateway.get_classroom_feedback(
            "S1001", "数学", period.start, period.end, teacher_context
        ),
    )
    metrics = calculate_learning_metrics(data)
    profile = build_learning_profile(student, "数学", period, metrics, rules_version="1.0.0")
    cases = HybridCaseRetriever(
        JsonCaseStore(PROJECT_ROOT / "data" / "cases"), min_relevance=0.2
    ).retrieve(profile)
    return data, metrics, profile, cases


def test_generated_report_is_evidence_bound_and_valid(gateway, teacher_context) -> None:
    data, metrics, profile, cases = _analysis_inputs(gateway, teacher_context)
    generator = DeterministicReportGenerator(prompt_version="1.0.0")

    report = generator.generate(data, metrics, profile, cases, include_parent_summary=False)
    validation = validate_report(
        report,
        data,
        metrics,
        profile,
        cases,
        parent_summary_requested=False,
    )

    assert validation.passed is True
    assert report.parent_communication_summary is None
    assert set(report.retrieved_case_ids) <= {item.case.case_id for item in cases}
    assert all(action.evidence_ids for action in report.recommended_actions)


def test_no_case_mode_degrades_without_fabricating_references(gateway, teacher_context) -> None:
    data, metrics, profile, _ = _analysis_inputs(gateway, teacher_context)
    report = DeterministicReportGenerator(prompt_version="1.0.0").generate(
        data, metrics, profile, [], include_parent_summary=True
    )

    assert report.retrieved_case_ids == []
    assert all(not action.reference_case_ids for action in report.recommended_actions)
    assert "暂无达到相关性阈值的历史案例" in report.uncertainties
    assert report.parent_communication_summary is not None
    assert "接下来教师将" in report.parent_communication_summary
    assert "建议家长" in report.parent_communication_summary
    assert "后续将结合课堂表现持续调整" in report.parent_communication_summary


def test_validator_rejects_tampered_fact_and_unknown_case(gateway, teacher_context) -> None:
    data, metrics, profile, cases = _analysis_inputs(gateway, teacher_context)
    report = DeterministicReportGenerator(prompt_version="1.0.0").generate(
        data, metrics, profile, cases, include_parent_summary=False
    )
    tampered = report.model_copy(
        update={
            "data_completeness": 0.1,
            "retrieved_case_ids": ["CASE-FAKE-9999"],
            "overall_summary": report.overall_summary + " 该学生天赋差。",
        }
    )

    validation = validate_report(
        tampered,
        data,
        metrics,
        profile,
        cases,
        parent_summary_requested=False,
    )

    assert validation.passed is False
    assert validation.numeric_errors
    assert validation.citation_errors
    assert any(item.code == "HARMFUL_LABEL" for item in validation.unsupported_claims)


def test_validator_rejects_persistent_trend_claim_from_two_scores(
    gateway, teacher_context
) -> None:
    data, _, original_profile, _ = _analysis_inputs(gateway, teacher_context)
    data = data.model_copy(update={"scores": data.scores[:2]})
    metrics = calculate_learning_metrics(data)
    profile = build_learning_profile(
        data.student,
        original_profile.subject,
        original_profile.period,
        metrics,
        rules_version="1.0.0",
    )
    cases = HybridCaseRetriever(
        JsonCaseStore(PROJECT_ROOT / "data" / "cases"), min_relevance=0.2
    ).retrieve(profile)
    report = DeterministicReportGenerator(prompt_version="1.0.0").generate(
        data, metrics, profile, cases, include_parent_summary=False
    )
    tampered = report.model_copy(
        update={"overall_summary": report.overall_summary + " 成绩持续下降。"}
    )

    validation = validate_report(
        tampered,
        data,
        metrics,
        profile,
        cases,
        parent_summary_requested=False,
    )

    assert validation.passed is False
    assert any(
        item.code == "INSUFFICIENT_CONTINUOUS_TREND"
        for item in validation.unsupported_claims
    )


def test_validator_rejects_action_not_supported_by_cited_case(gateway, teacher_context) -> None:
    data, metrics, profile, cases = _analysis_inputs(gateway, teacher_context)
    report = DeterministicReportGenerator(prompt_version="1.0.0").generate(
        data, metrics, profile, cases, include_parent_summary=False
    )
    first = report.recommended_actions[0].model_copy(update={"action": "每天额外刷题五小时"})
    tampered = report.model_copy(
        update={"recommended_actions": [first, *report.recommended_actions[1:]]}
    )

    validation = validate_report(
        tampered,
        data,
        metrics,
        profile,
        cases,
        parent_summary_requested=False,
    )

    assert validation.passed is False
    assert any(item.code == "UNSUPPORTED_CASE_ACTION" for item in validation.citation_errors)
