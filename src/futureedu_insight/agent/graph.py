from __future__ import annotations

from typing import TypedDict, cast

from langgraph.graph import END, START, StateGraph

from futureedu_insight.agent.report_generator import ReportGenerator
from futureedu_insight.agent.request_parser import DeterministicRequestParser
from futureedu_insight.domain.errors import PermissionDeniedError
from futureedu_insight.domain.models import (
    LearningDataBundle,
    LearningMetrics,
    LearningProfile,
    LearningReport,
    ParsedAnalysisRequest,
    ReportValidationResult,
    RetrievedCase,
    StudentProfile,
    TaskStatus,
    TeacherExecutionContext,
)
from futureedu_insight.gateways.learning_data import LearningDataGateway
from futureedu_insight.rag.hybrid_retriever import HybridCaseRetriever
from futureedu_insight.tools import (
    build_learning_profile,
    calculate_learning_metrics,
    validate_report,
)


class LearningAnalysisState(TypedDict, total=False):
    query: str
    context: TeacherExecutionContext
    include_parent_summary: bool
    status: TaskStatus
    parsed_request: ParsedAnalysisRequest
    student: StudentProfile
    candidates: list[StudentProfile]
    data: LearningDataBundle
    metrics: LearningMetrics
    profile: LearningProfile
    retrieved_cases: list[RetrievedCase]
    report: LearningReport
    validation: ReportValidationResult
    generation_attempt: int
    clarification: dict[str, object]
    error_code: str
    error_message: str


class LearningInsightAgent:
    """Single-student analysis workflow with explicit, testable state transitions."""

    def __init__(
        self,
        gateway: LearningDataGateway,
        retriever: HybridCaseRetriever,
        *,
        parser: DeterministicRequestParser | None = None,
        generator: ReportGenerator,
        profile_rules_version: str = "1.0.0",
        max_report_retries: int = 2,
    ) -> None:
        self.gateway = gateway
        self.retriever = retriever
        self.parser = parser or DeterministicRequestParser()
        self.generator = generator
        self.profile_rules_version = profile_rules_version
        self.max_report_retries = max_report_retries
        self.graph = self._build_graph()

    def _parse_request(self, state: LearningAnalysisState) -> LearningAnalysisState:
        parsed = self.parser.parse(
            state["query"],
            include_parent_summary=state.get("include_parent_summary", False),
        )
        if not parsed.supported:
            return {
                "parsed_request": parsed,
                "status": TaskStatus.FAILED,
                "error_code": "UNSUPPORTED_REQUEST",
                "error_message": parsed.unsupported_reason or "不支持的请求",
            }
        if parsed.needs_clarification:
            return {
                "parsed_request": parsed,
                "status": TaskStatus.NEEDS_CLARIFICATION,
                "clarification": {"missing_fields": parsed.missing_fields},
            }
        return {"parsed_request": parsed, "status": TaskStatus.RUNNING}

    @staticmethod
    def _route_after_parse(state: LearningAnalysisState) -> str:
        return "resolve" if state["status"] == TaskStatus.RUNNING else "end"

    def _resolve_student(self, state: LearningAnalysisState) -> LearningAnalysisState:
        parsed = state["parsed_request"]
        candidates = self.gateway.resolve_students(
            parsed.student_identifier or "",
            parsed.class_context,
            state["context"],
        )
        if not candidates:
            return {
                "candidates": [],
                "status": TaskStatus.FAILED,
                "error_code": "STUDENT_NOT_FOUND",
                "error_message": "在当前教师可访问范围内未找到该学生",
            }
        if len(candidates) > 1:
            return {
                "candidates": candidates,
                "status": TaskStatus.NEEDS_CLARIFICATION,
                "clarification": {
                    "field": "student_identifier",
                    "candidates": [
                        {
                            "student_id": item.student_id,
                            "display_name": item.display_name,
                            "class_name": item.class_name,
                        }
                        for item in candidates
                    ],
                },
            }
        return {"candidates": candidates, "student": candidates[0]}

    @staticmethod
    def _route_after_resolution(state: LearningAnalysisState) -> str:
        return "collect" if "student" in state else "end"

    def _collect_data(self, state: LearningAnalysisState) -> LearningAnalysisState:
        parsed = state["parsed_request"]
        student_id = state["student"].student_id
        period = parsed.period
        if period is None or parsed.subject is None:  # protected by parser model validation
            return {
                "status": TaskStatus.FAILED,
                "error_code": "INVALID_PARSED_REQUEST",
                "error_message": "解析结果缺少学科或时间范围",
            }
        try:
            student = self.gateway.get_student_profile(student_id, state["context"])
            scores = self.gateway.get_score_records(
                student_id, parsed.subject, period.start, period.end, state["context"]
            )
            homework = self.gateway.get_homework_records(
                student_id, parsed.subject, period.start, period.end, state["context"]
            )
            attendance = self.gateway.get_attendance_records(
                student_id, period.start, period.end, state["context"]
            )
            feedback = self.gateway.get_classroom_feedback(
                student_id, parsed.subject, period.start, period.end, state["context"]
            )
        except PermissionDeniedError:
            return {
                "status": TaskStatus.PERMISSION_DENIED,
                "error_code": "PERMISSION_DENIED",
                "error_message": "无权访问该学生",
            }

        records = [*scores, *homework, *attendance, *feedback]
        return {
            "data": LearningDataBundle(
                student=student,
                scores=scores,
                homework=homework,
                attendance=attendance,
                feedback=feedback,
                source_record_ids=[item.record_id for item in records],
            )
        }

    @staticmethod
    def _assess_sufficiency(state: LearningAnalysisState) -> LearningAnalysisState:
        if "data" not in state:
            return {}
        if not state["data"].scores:
            return {
                "status": TaskStatus.INSUFFICIENT_DATA,
                "error_code": "INSUFFICIENT_SCORE_DATA",
                "error_message": "分析时间范围内没有该学科的成绩记录",
            }
        return {"status": TaskStatus.RUNNING}

    @staticmethod
    def _route_after_sufficiency(state: LearningAnalysisState) -> str:
        return "metrics" if state["status"] == TaskStatus.RUNNING else "end"

    @staticmethod
    def _calculate_metrics(state: LearningAnalysisState) -> LearningAnalysisState:
        return {"metrics": calculate_learning_metrics(state["data"])}

    def _build_profile(self, state: LearningAnalysisState) -> LearningAnalysisState:
        parsed = state["parsed_request"]
        assert parsed.period is not None and parsed.subject is not None
        return {
            "profile": build_learning_profile(
                state["student"],
                parsed.subject,
                parsed.period,
                state["metrics"],
                rules_version=self.profile_rules_version,
            )
        }

    def _retrieve_cases(self, state: LearningAnalysisState) -> LearningAnalysisState:
        return {"retrieved_cases": self.retriever.retrieve(state["profile"])}

    def _generate_report(self, state: LearningAnalysisState) -> LearningAnalysisState:
        report = self.generator.generate(
            state["data"],
            state["metrics"],
            state["profile"],
            state.get("retrieved_cases", []),
            include_parent_summary=state.get("include_parent_summary", False),
        )
        return {
            "report": report,
            "generation_attempt": state.get("generation_attempt", 0) + 1,
        }

    @staticmethod
    def _validate_report(state: LearningAnalysisState) -> LearningAnalysisState:
        result = validate_report(
            state["report"],
            state["data"],
            state["metrics"],
            state["profile"],
            state.get("retrieved_cases", []),
            parent_summary_requested=state.get("include_parent_summary", False),
        )
        return {"validation": result}

    def _route_after_validation(self, state: LearningAnalysisState) -> str:
        if state["validation"].passed:
            return "success"
        if (
            state["validation"].retryable
            and state.get("generation_attempt", 0) <= self.max_report_retries
        ):
            return "retry"
        return "failure"

    @staticmethod
    def _mark_success(_: LearningAnalysisState) -> LearningAnalysisState:
        return {"status": TaskStatus.AWAITING_CONFIRMATION}

    @staticmethod
    def _mark_validation_failure(_: LearningAnalysisState) -> LearningAnalysisState:
        return {
            "status": TaskStatus.FAILED,
            "error_code": "REPORT_VALIDATION_FAILED",
            "error_message": "报告事实校验未通过",
        }

    def _build_graph(self):
        workflow = StateGraph(LearningAnalysisState)
        workflow.add_node("parse", self._parse_request)
        workflow.add_node("resolve", self._resolve_student)
        workflow.add_node("collect", self._collect_data)
        workflow.add_node("sufficiency", self._assess_sufficiency)
        workflow.add_node("metrics", self._calculate_metrics)
        workflow.add_node("profile", self._build_profile)
        workflow.add_node("retrieve", self._retrieve_cases)
        workflow.add_node("generate", self._generate_report)
        workflow.add_node("validate", self._validate_report)
        workflow.add_node("success", self._mark_success)
        workflow.add_node("validation_failure", self._mark_validation_failure)

        workflow.add_edge(START, "parse")
        workflow.add_conditional_edges(
            "parse", self._route_after_parse, {"resolve": "resolve", "end": END}
        )
        workflow.add_conditional_edges(
            "resolve",
            self._route_after_resolution,
            {"collect": "collect", "end": END},
        )
        workflow.add_edge("collect", "sufficiency")
        workflow.add_conditional_edges(
            "sufficiency",
            self._route_after_sufficiency,
            {"metrics": "metrics", "end": END},
        )
        workflow.add_edge("metrics", "profile")
        workflow.add_edge("profile", "retrieve")
        workflow.add_edge("retrieve", "generate")
        workflow.add_edge("generate", "validate")
        workflow.add_conditional_edges(
            "validate",
            self._route_after_validation,
            {
                "success": "success",
                "retry": "generate",
                "failure": "validation_failure",
            },
        )
        workflow.add_edge("success", END)
        workflow.add_edge("validation_failure", END)
        return workflow.compile()

    def analyze(
        self,
        query: str,
        context: TeacherExecutionContext,
        *,
        include_parent_summary: bool = False,
    ) -> LearningAnalysisState:
        result = self.graph.invoke(
            {
                "query": query,
                "context": context,
                "include_parent_summary": include_parent_summary,
                "status": TaskStatus.RUNNING,
                "generation_attempt": 0,
            }
        )
        return cast(LearningAnalysisState, result)
