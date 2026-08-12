from __future__ import annotations

import logging
from collections.abc import Callable
from uuid import uuid4

from futureedu_insight.agent.graph import LearningAnalysisState, LearningInsightAgent
from futureedu_insight.domain.models import (
    LearningDataBundle,
    LearningReport,
    ReportValidationResult,
    TaskRecord,
    TaskStatus,
    TeacherExecutionContext,
)
from futureedu_insight.gateways.learning_data import LearningDataGateway
from futureedu_insight.infrastructure.observability import (
    log_event,
    metrics_registry,
    request_id_var,
)
from futureedu_insight.infrastructure.repository import SQLiteTaskRepository
from futureedu_insight.rag.hybrid_retriever import HybridCaseRetriever
from futureedu_insight.tools import (
    build_learning_profile,
    calculate_learning_metrics,
    validate_report,
)


class AnalysisService:
    def __init__(
        self,
        agent: LearningInsightAgent,
        gateway: LearningDataGateway,
        retriever: HybridCaseRetriever,
        repository: SQLiteTaskRepository,
        *,
        profile_rules_version: str,
        agent_factory: Callable[[TeacherExecutionContext], LearningInsightAgent] | None = None,
        gateway_factory: Callable[[TeacherExecutionContext], LearningDataGateway] | None = None,
    ) -> None:
        self.agent = agent
        self.gateway = gateway
        self.retriever = retriever
        self.repository = repository
        self.profile_rules_version = profile_rules_version
        self.agent_factory = agent_factory
        self.gateway_factory = gateway_factory

    def create_task(
        self,
        query: str,
        session_id: str,
        teacher_id: str,
        *,
        include_parent_summary: bool,
    ) -> tuple[TaskRecord, TeacherExecutionContext]:
        context = TeacherExecutionContext(
            teacher_id=teacher_id,
            request_id=f"REQ-{uuid4().hex[:12].upper()}",
            session_id=session_id,
        )
        task_id = f"TASK-{uuid4().hex[:12].upper()}"
        task = self.repository.create_task(task_id, query, include_parent_summary, context)
        return task, context

    def run_task(self, task_id: str, context: TeacherExecutionContext) -> None:
        token = request_id_var.set(context.request_id)
        query, include_parent_summary, _ = self.repository.task_input(task_id, context.teacher_id)
        try:
            with metrics_registry.timer("analysis_latency"):
                agent = self.agent_factory(context) if self.agent_factory else self.agent
                state = agent.analyze(
                    query,
                    context,
                    include_parent_summary=include_parent_summary,
                )
            self._persist_state(task_id, context, state)
            if "data" in state and "profile" in state:
                data = state["data"]
                profile = state["profile"]
                self.repository.audit(
                    context,
                    "analysis_evidence_used",
                    "task",
                    task_id,
                    {
                        "student_id": data.student.student_id,
                        "subject": profile.subject,
                        "period_start": profile.period.start.isoformat(),
                        "period_end": profile.period.end.isoformat(),
                        "tools": [
                            "get_student_profile",
                            "get_score_records",
                            "get_homework_records",
                            "get_attendance_records",
                            "get_classroom_feedback",
                        ],
                        "source_record_count": len(state["metrics"].evidence_record_ids),
                        "case_ids": [
                            item.case.case_id for item in state.get("retrieved_cases", [])
                        ],
                        "model_version": (
                            state["report"].model_version if "report" in state else None
                        ),
                        "prompt_version": (
                            state["report"].prompt_version if "report" in state else None
                        ),
                    },
                )
            metrics_registry.increment(f"task_status_{state['status'].value}")
            log_event(
                logging.getLogger("futureedu_insight.analysis"),
                "analysis_task_finished",
                task_id=task_id,
                status=state["status"].value,
                retrieved_case_count=len(state.get("retrieved_cases", [])),
                generation_attempt=state.get("generation_attempt", 0),
            )
        except Exception as exc:  # task boundary must persist a terminal state
            metrics_registry.increment("task_unexpected_error")
            self.repository.update_task_result(
                task_id,
                context,
                status=TaskStatus.FAILED,
                error_code="UNEXPECTED_ERROR",
                error_message=str(exc),
            )
            log_event(
                logging.getLogger("futureedu_insight.analysis"),
                "analysis_task_failed",
                task_id=task_id,
                error_type=type(exc).__name__,
            )
        finally:
            request_id_var.reset(token)

    def _persist_state(
        self,
        task_id: str,
        context: TeacherExecutionContext,
        state: LearningAnalysisState,
    ) -> None:
        execution = {
            "workflow_status": state["status"].value,
            "tools_called": (
                [
                    "get_student_profile",
                    "get_score_records",
                    "get_homework_records",
                    "get_attendance_records",
                    "get_classroom_feedback",
                ]
                if "data" in state
                else []
            ),
            "metrics": (state["metrics"].model_dump(mode="json") if "metrics" in state else None),
            "profile": (state["profile"].model_dump(mode="json") if "profile" in state else None),
            "retrieved_cases": [
                {
                    "case_id": item.case.case_id,
                    "relevance_score": item.relevance_score,
                    "similarity_reasons": item.similarity_reasons,
                    "citation_text": item.citation_text,
                }
                for item in state.get("retrieved_cases", [])
            ],
            "generation_attempt": state.get("generation_attempt", 0),
        }
        self.repository.update_task_result(
            task_id,
            context,
            status=state["status"],
            report=state.get("report"),
            validation=state.get("validation"),
            execution=execution,
            clarification=state.get("clarification"),
            error_code=state.get("error_code"),
            error_message=state.get("error_message"),
        )

    def clarify_task(
        self,
        task_id: str,
        teacher_id: str,
        additional_information: str,
    ) -> TeacherExecutionContext:
        query, _, context = self.repository.task_input(task_id, teacher_id)
        self.repository.restart_task(
            task_id,
            context,
            query=f"{query} {additional_information.strip()}",
        )
        return context

    def regenerate_task(self, task_id: str, teacher_id: str) -> TeacherExecutionContext:
        _, _, context = self.repository.task_input(task_id, teacher_id)
        self.repository.restart_task(task_id, context)
        return context

    def validate_teacher_edit(
        self,
        report: LearningReport,
        context: TeacherExecutionContext,
        *,
        parent_summary_requested: bool,
    ) -> ReportValidationResult:
        period = report.period
        gateway = self.gateway_factory(context) if self.gateway_factory else self.gateway
        student = gateway.get_student_profile(report.student_id, context)
        data = LearningDataBundle(
            student=student,
            scores=gateway.get_score_records(
                report.student_id,
                report.subject,
                period.start,
                period.end,
                context,
            ),
            homework=gateway.get_homework_records(
                report.student_id,
                report.subject,
                period.start,
                period.end,
                context,
            ),
            attendance=gateway.get_attendance_records(
                report.student_id, period.start, period.end, context
            ),
            feedback=gateway.get_classroom_feedback(
                report.student_id,
                report.subject,
                period.start,
                period.end,
                context,
            ),
        )
        metrics = calculate_learning_metrics(data)
        profile = build_learning_profile(
            student,
            report.subject,
            period,
            metrics,
            rules_version=self.profile_rules_version,
        )
        cases = self.retriever.retrieve(profile)
        return validate_report(
            report,
            data,
            metrics,
            profile,
            cases,
            parent_summary_requested=parent_summary_requested,
        )
