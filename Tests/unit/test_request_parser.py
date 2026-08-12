from datetime import date

from futureedu_insight.agent.request_parser import DeterministicRequestParser
from futureedu_insight.domain.models import AnalysisFocus


def test_parser_extracts_required_analysis_fields() -> None:
    parser = DeterministicRequestParser(today=date(2026, 8, 9))

    result = parser.parse("生成学生 S1001 2026年3月数学学情报告，重点看成绩和作业")

    assert result.supported is True
    assert result.needs_clarification is False
    assert result.student_identifier == "S1001"
    assert result.subject == "数学"
    assert result.period is not None
    assert result.period.start == date(2026, 3, 1)
    assert result.period.end == date(2026, 3, 31)
    assert result.analysis_focus == [AnalysisFocus.SCORE, AnalysisFocus.HOMEWORK]


def test_parser_returns_structured_clarification() -> None:
    parser = DeterministicRequestParser(today=date(2026, 8, 9))

    result = parser.parse("分析学生张晨的数学学情")

    assert result.supported is True
    assert result.needs_clarification is True
    assert result.missing_fields == ["period"]
    assert result.student_identifier == "张晨"


def test_parser_rejects_out_of_scope_request() -> None:
    result = DeterministicRequestParser().parse("帮我查询教研资料")

    assert result.supported is False
    assert "单个学生" in (result.unsupported_reason or "")


def test_parent_summary_is_only_enabled_explicitly() -> None:
    parser = DeterministicRequestParser(today=date(2026, 8, 9))
    query = "生成学生 S1001 2026年3月数学学情报告"

    assert parser.parse(query).include_parent_summary is False
    assert parser.parse(query, include_parent_summary=True).include_parent_summary is True


def test_parser_handles_classmate_wording_and_class_context() -> None:
    parser = DeterministicRequestParser(today=date(2026, 8, 9))

    result = parser.parse("分析张晨同学2026年3月数学成绩，八年级数学一班")

    assert result.student_identifier == "张晨"
    assert result.class_context == "八年级数学一班"
