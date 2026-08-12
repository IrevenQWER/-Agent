from __future__ import annotations

from datetime import UTC, date, datetime
from enum import StrEnum
from typing import Any, Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def utc_now() -> datetime:
    return datetime.now(UTC)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


ToolData = TypeVar("ToolData")


class ToolResult(StrictModel, Generic[ToolData]):
    success: bool
    data: ToolData | None = None
    error_code: str | None = None
    error_message: str | None = None
    source_record_ids: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_result(self) -> ToolResult[ToolData]:
        if self.success and self.error_code:
            raise ValueError("成功结果不能包含错误码")
        if not self.success and not self.error_code:
            raise ValueError("失败结果必须包含错误码")
        return self


class AnalysisFocus(StrEnum):
    SCORE = "成绩"
    HOMEWORK = "作业"
    ATTENDANCE = "考勤"
    CLASSROOM = "课堂表现"
    COMPREHENSIVE = "综合"


class TaskStatus(StrEnum):
    RUNNING = "running"
    NEEDS_CLARIFICATION = "needs_clarification"
    PERMISSION_DENIED = "permission_denied"
    INSUFFICIENT_DATA = "insufficient_data"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    COMPLETED = "completed"
    FAILED = "failed"


class ConfirmationStatus(StrEnum):
    UNCONFIRMED = "unconfirmed"
    CONFIRMED = "confirmed"


class ScoreTrend(StrEnum):
    IMPROVING = "improving"
    STABLE = "stable"
    DECLINING = "declining"
    INSUFFICIENT = "insufficient"


class ConfidenceLevel(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class TeacherExecutionContext(StrictModel):
    teacher_id: str = Field(min_length=1, max_length=64)
    request_id: str = Field(min_length=1, max_length=128)
    session_id: str = Field(min_length=1, max_length=128)


class DateRange(StrictModel):
    start: date
    end: date

    @model_validator(mode="after")
    def validate_order(self) -> DateRange:
        if self.end < self.start:
            raise ValueError("结束日期不能早于开始日期")
        if (self.end - self.start).days > 366:
            raise ValueError("单次分析时间范围不能超过366天")
        return self


class ParsedAnalysisRequest(StrictModel):
    supported: bool
    unsupported_reason: str | None = None
    needs_clarification: bool = False
    missing_fields: list[str] = Field(default_factory=list)
    student_identifier: str | None = None
    class_context: str | None = None
    subject: str | None = None
    period: DateRange | None = None
    analysis_focus: list[AnalysisFocus] = Field(default_factory=list)
    include_parent_summary: bool = False

    @model_validator(mode="after")
    def validate_supported_fields(self) -> ParsedAnalysisRequest:
        if not self.supported:
            if not self.unsupported_reason:
                raise ValueError("不支持的请求必须给出原因")
            return self
        if self.needs_clarification:
            if not self.missing_fields:
                raise ValueError("需要补充信息时必须列出缺失字段")
            return self
        if not self.student_identifier:
            raise ValueError("缺少学生标识")
        if not self.subject:
            raise ValueError("缺少学科")
        if not self.period:
            raise ValueError("缺少分析时间范围")
        if not self.analysis_focus:
            self.analysis_focus = [AnalysisFocus.COMPREHENSIVE]
        return self


class StudentProfile(StrictModel):
    student_id: str
    display_name: str
    grade: str
    class_id: str
    class_name: str
    campus_id: str
    enrollment_status: Literal["active", "inactive"] = "active"


class ScoreRecord(StrictModel):
    record_id: str
    student_id: str
    exam_id: str
    exam_name: str
    subject: str
    score: float = Field(ge=0)
    full_score: float = Field(gt=0)
    class_average: float = Field(ge=0)
    rank: int = Field(ge=1)
    participant_count: int = Field(ge=1)
    exam_date: date
    knowledge_scores: dict[str, float] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_score(self) -> ScoreRecord:
        if self.score > self.full_score or self.class_average > self.full_score:
            raise ValueError("成绩或班级均分不能超过满分")
        if self.rank > self.participant_count:
            raise ValueError("排名不能超过参考人数")
        for point, rate in self.knowledge_scores.items():
            if not point or rate < 0 or rate > 1:
                raise ValueError("知识点名称不能为空，得分率必须位于[0,1]")
        return self


class HomeworkRecord(StrictModel):
    record_id: str
    student_id: str
    subject: str
    homework_date: date
    submitted: bool
    accuracy_rate: float | None = Field(default=None, ge=0, le=1)
    corrected: bool | None = None
    knowledge_tags: list[str] = Field(default_factory=list)
    teacher_comment: str | None = None

    @model_validator(mode="after")
    def validate_submission(self) -> HomeworkRecord:
        if not self.submitted and self.accuracy_rate is not None:
            raise ValueError("未提交作业不能包含正确率")
        return self


class AttendanceRecord(StrictModel):
    record_id: str
    student_id: str
    course_id: str
    lesson_date: date
    status: Literal["present", "late", "leave", "absent"]
    late_minutes: int = Field(default=0, ge=0)
    leave_type: str | None = None


class ClassroomFeedback(StrictModel):
    record_id: str
    student_id: str
    teacher_id: str
    course_id: str
    feedback_date: date
    performance_tags: list[str] = Field(default_factory=list)
    feedback_text: str | None = None


class LearningDataBundle(StrictModel):
    student: StudentProfile
    scores: list[ScoreRecord] = Field(default_factory=list)
    homework: list[HomeworkRecord] = Field(default_factory=list)
    attendance: list[AttendanceRecord] = Field(default_factory=list)
    feedback: list[ClassroomFeedback] = Field(default_factory=list)
    source_record_ids: list[str] = Field(default_factory=list)


class LearningMetrics(StrictModel):
    normalized_scores: list[float] = Field(default_factory=list)
    score_delta: float | None = None
    score_slope: float | None = None
    score_trend: ScoreTrend = ScoreTrend.INSUFFICIENT
    class_average_gap: float | None = None
    rank_percentile_change: float | None = None
    homework_submission_rate: float | None = Field(default=None, ge=0, le=1)
    homework_accuracy_rate: float | None = Field(default=None, ge=0, le=1)
    correction_rate: float | None = Field(default=None, ge=0, le=1)
    attendance_rate: float | None = Field(default=None, ge=0, le=1)
    weak_knowledge_points: list[str] = Field(default_factory=list)
    classroom_tags: list[str] = Field(default_factory=list)
    data_completeness: float = Field(ge=0, le=1)
    evidence_record_ids: list[str] = Field(default_factory=list)


class LearningProfile(StrictModel):
    student_id: str
    grade: str
    subject: str
    period: DateRange
    rules_version: str
    score_trend: ScoreTrend
    score_delta: float | None = None
    rank_percentile_change: float | None = None
    homework_submission_rate: float | None = None
    homework_accuracy_rate: float | None = None
    correction_rate: float | None = None
    attendance_rate: float | None = None
    weak_knowledge_points: list[str] = Field(default_factory=list)
    classroom_tags: list[str] = Field(default_factory=list)
    learning_tags: list[str] = Field(default_factory=list)
    data_completeness: float
    evidence_record_ids: list[str] = Field(default_factory=list)


class HistoricalCase(StrictModel):
    case_id: str
    grade: str
    subject: str
    problem_types: list[str]
    score_trend: ScoreTrend
    profile_text: str
    intervention: str
    observed_metric: str
    before_value: float | None = None
    after_value: float | None = None
    observation_period: str
    evidence_quality: ConfidenceLevel
    applicable_conditions: list[str] = Field(default_factory=list)
    unsuitable_conditions: list[str] = Field(default_factory=list)
    approval_status: Literal["approved"] = "approved"
    version: str


class RetrievedCase(StrictModel):
    case: HistoricalCase
    vector_score: float = Field(ge=0, le=1)
    keyword_score: float = Field(ge=0, le=1)
    relevance_score: float = Field(ge=0, le=1)
    similarity_reasons: list[str] = Field(default_factory=list)
    citation_text: str


class EvidenceReference(StrictModel):
    evidence_id: str
    evidence_type: Literal["business_record", "metric", "case"]
    description: str


class EvidenceBasedConclusion(StrictModel):
    conclusion: str
    evidence_ids: list[str] = Field(min_length=1)


class RecommendedAction(StrictModel):
    action: str
    reason: str
    evidence_ids: list[str] = Field(default_factory=list)
    reference_case_ids: list[str] = Field(default_factory=list)
    duration: str
    observation_metric: str
    confidence_score: float = Field(ge=0, le=1)
    confidence_level: ConfidenceLevel


class LearningReport(StrictModel):
    report_id: str
    student_id: str
    student_display_name: str
    subject: str
    period: DateRange
    overall_summary: str
    data_completeness: float = Field(ge=0, le=1)
    score_analysis: str | None = None
    homework_analysis: str | None = None
    attendance_analysis: str | None = None
    classroom_analysis: str | None = None
    weak_knowledge_points: list[str] = Field(default_factory=list)
    strengths: list[EvidenceBasedConclusion] = Field(default_factory=list)
    risks: list[EvidenceBasedConclusion] = Field(default_factory=list)
    retrieved_case_ids: list[str] = Field(default_factory=list)
    recommended_actions: list[RecommendedAction] = Field(default_factory=list)
    parent_communication_summary: str | None = None
    uncertainties: list[str] = Field(default_factory=list)
    evidence: list[EvidenceReference] = Field(default_factory=list)
    model_version: str
    prompt_version: str
    generated_at: datetime = Field(default_factory=utc_now)

    @field_validator("overall_summary")
    @classmethod
    def summary_not_empty(cls, value: str) -> str:
        if not value:
            raise ValueError("报告摘要不能为空")
        return value


class ValidationIssue(StrictModel):
    code: str
    field: str
    message: str
    expected: Any | None = None
    actual: Any | None = None


class ReportValidationResult(StrictModel):
    passed: bool
    numeric_errors: list[ValidationIssue] = Field(default_factory=list)
    trend_errors: list[ValidationIssue] = Field(default_factory=list)
    citation_errors: list[ValidationIssue] = Field(default_factory=list)
    privacy_errors: list[ValidationIssue] = Field(default_factory=list)
    unsupported_claims: list[ValidationIssue] = Field(default_factory=list)
    retryable: bool = False

    @model_validator(mode="after")
    def derive_passed(self) -> ReportValidationResult:
        has_errors = any(
            (
                self.numeric_errors,
                self.trend_errors,
                self.citation_errors,
                self.privacy_errors,
                self.unsupported_claims,
            )
        )
        if self.passed == has_errors:
            raise ValueError("passed 必须与错误列表状态一致")
        return self


class ReportRecord(StrictModel):
    report: LearningReport
    validation: ReportValidationResult
    confirmation_status: ConfirmationStatus = ConfirmationStatus.UNCONFIRMED
    confirmed_by: str | None = None
    confirmed_at: datetime | None = None
    teacher_edits: dict[str, Any] = Field(default_factory=dict)


class TaskRecord(StrictModel):
    task_id: str
    request_id: str
    session_id: str
    teacher_id: str
    status: TaskStatus
    report_id: str | None = None
    clarification: dict[str, Any] | None = None
    error_code: str | None = None
    error_message: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
