from datetime import date

import pytest
from pydantic import ValidationError

from futureedu_insight.domain.models import (
    DateRange,
    ParsedAnalysisRequest,
    ReportValidationResult,
    ValidationIssue,
)


def test_date_range_rejects_reverse_order() -> None:
    with pytest.raises(ValidationError, match="结束日期不能早于开始日期"):
        DateRange(start=date(2026, 4, 1), end=date(2026, 3, 1))


def test_supported_request_requires_student_subject_and_period() -> None:
    with pytest.raises(ValidationError, match="缺少学生标识"):
        ParsedAnalysisRequest(supported=True)


def test_unsupported_request_requires_reason() -> None:
    with pytest.raises(ValidationError, match="不支持的请求必须给出原因"):
        ParsedAnalysisRequest(supported=False)


def test_validation_result_must_match_issue_lists() -> None:
    issue = ValidationIssue(code="numeric_mismatch", field="score", message="分数不一致")
    with pytest.raises(ValidationError, match="passed 必须与错误列表状态一致"):
        ReportValidationResult(passed=True, numeric_errors=[issue])

    valid = ReportValidationResult(passed=False, numeric_errors=[issue])
    assert not valid.passed
