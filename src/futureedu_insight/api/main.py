from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from time import perf_counter
from typing import Annotated
from uuid import uuid4

import uvicorn
from fastapi import (
    BackgroundTasks,
    Depends,
    FastAPI,
    Header,
    HTTPException,
    Query,
    Request,
    Response,
    status,
)

from futureedu_insight.adapters import McpLearningDataAdapter, SQLiteLearningDataGateway
from futureedu_insight.agent import LearningInsightAgent
from futureedu_insight.agent.report_generator import (
    DeterministicReportGenerator,
    OllamaReportGenerator,
    OpenAICompatibleReportGenerator,
)
from futureedu_insight.api.schemas import (
    ClarificationRequest,
    ConfirmationRequest,
    ConfirmationResponse,
    CreateTaskRequest,
    CreateTaskResponse,
    EvidenceItem,
    TaskResponse,
)
from futureedu_insight.config import Settings, get_settings
from futureedu_insight.domain.errors import PermissionDeniedError, StudentNotFoundError
from futureedu_insight.domain.models import ConfirmationStatus, TaskStatus
from futureedu_insight.infrastructure.observability import (
    configure_logging,
    metrics_registry,
    request_id_var,
)
from futureedu_insight.infrastructure.repository import SQLiteTaskRepository
from futureedu_insight.infrastructure.seed import seed_database
from futureedu_insight.rag.case_store import JsonCaseStore
from futureedu_insight.rag.chroma_index import ChromaCaseIndex
from futureedu_insight.rag.hybrid_retriever import HybridCaseRetriever
from futureedu_insight.services import AnalysisService
from futureedu_insight.services.report_export import report_to_markdown


def authenticated_teacher_id(
    x_teacher_id: Annotated[str | None, Header(alias="X-Teacher-ID")] = None,
) -> str:
    if not x_teacher_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="缺少教师认证上下文",
        )
    return x_teacher_id


def get_analysis_service(request: Request) -> AnalysisService:
    return request.app.state.analysis_service


def build_service(settings: Settings) -> AnalysisService:
    gateway = SQLiteLearningDataGateway(settings.database_path)
    case_store = JsonCaseStore(settings.cases_path)
    vector_index = None
    try:
        vector_index = ChromaCaseIndex(settings.chroma_path)
        vector_index.sync(case_store.load())
    except RuntimeError as exc:
        logging.getLogger("futureedu_insight.rag").warning(
            "Chroma unavailable, using in-memory vector fallback: %s", exc
        )
    retriever = HybridCaseRetriever(
        case_store,
        min_relevance=settings.min_case_relevance,
        final_count=settings.final_case_count,
        vector_index=vector_index,
    )
    if settings.model_provider == "openai":
        generator = OpenAICompatibleReportGenerator(
            base_url=settings.model_base_url,
            model_name=settings.model_name,
            api_key=settings.model_api_key,
            timeout_seconds=settings.model_timeout_seconds,
            prompt_path=settings.report_prompt_path,
        )
    elif settings.model_provider == "ollama":
        generator = OllamaReportGenerator(
            base_url=settings.model_base_url,
            model_name=settings.model_name,
            timeout_seconds=settings.model_timeout_seconds,
            prompt_path=settings.report_prompt_path,
        )
    else:
        generator = DeterministicReportGenerator(
            prompt_version=settings.prompt_version,
            data_completeness_weight=settings.data_completeness_weight,
            retrieval_score_weight=settings.retrieval_score_weight,
            evidence_quality_weight=settings.evidence_quality_weight,
        )
    agent = LearningInsightAgent(
        gateway,
        retriever,
        generator=generator,
        profile_rules_version=settings.profile_rules_version,
        max_report_retries=settings.max_report_retries,
    )
    agent_factory = None
    gateway_factory = None
    if settings.learning_data_backend == "mcp_stdio":

        def gateway_factory(context):
            return McpLearningDataAdapter(settings.database_path, context)

        def agent_factory(context):
            return LearningInsightAgent(
                gateway_factory(context),
                retriever,
                generator=generator,
                profile_rules_version=settings.profile_rules_version,
                max_report_retries=settings.max_report_retries,
            )

    return AnalysisService(
        agent,
        gateway,
        retriever,
        SQLiteTaskRepository(settings.database_path),
        profile_rules_version=settings.profile_rules_version,
        agent_factory=agent_factory,
        gateway_factory=gateway_factory,
    )


def create_app(settings: Settings | None = None) -> FastAPI:
    configured = settings or get_settings()

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        configure_logging()
        configured.ensure_runtime_directories()
        seed_database(configured.database_path, reset=False)
        application.state.analysis_service = build_service(configured)
        yield

    application = FastAPI(
        title="FutureEdu Learning Insight Agent API",
        version="0.1.0",
        lifespan=lifespan,
    )

    @application.middleware("http")
    async def request_context(request: Request, call_next):
        request_id = request.headers.get("X-Request-ID") or f"HTTP-{uuid4().hex[:12].upper()}"
        token = request_id_var.set(request_id)
        started = perf_counter()
        try:
            return await call_next(request)
        finally:
            metrics_registry.increment("http_requests")
            metrics_registry.counters["http_request_count"] += 1
            metrics_registry.latency_sum_ms["http_request"] += (perf_counter() - started) * 1000
            request_id_var.reset(token)

    @application.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @application.get("/metrics")
    def metrics() -> dict[str, float | int]:
        return metrics_registry.snapshot()

    @application.post(
        "/api/v1/analysis/tasks",
        response_model=CreateTaskResponse,
        status_code=status.HTTP_202_ACCEPTED,
    )
    def create_analysis_task(
        payload: CreateTaskRequest,
        background_tasks: BackgroundTasks,
        teacher_id: Annotated[str, Depends(authenticated_teacher_id)],
        analysis: Annotated[AnalysisService, Depends(get_analysis_service)],
    ) -> CreateTaskResponse:
        task, context = analysis.create_task(
            payload.query,
            payload.session_id,
            teacher_id,
            include_parent_summary=payload.include_parent_summary,
        )
        background_tasks.add_task(analysis.run_task, task.task_id, context)
        return CreateTaskResponse(
            request_id=task.request_id,
            task_id=task.task_id,
            status=TaskStatus.RUNNING.value,
        )

    @application.get("/api/v1/analysis/tasks/{task_id}", response_model=TaskResponse)
    def get_analysis_task(
        task_id: str,
        teacher_id: Annotated[str, Depends(authenticated_teacher_id)],
        analysis: Annotated[AnalysisService, Depends(get_analysis_service)],
    ) -> TaskResponse:
        try:
            task = analysis.repository.get_task(task_id, teacher_id)
            report_record = (
                analysis.repository.get_report(task.report_id, teacher_id)
                if task.report_id
                else None
            )
            return TaskResponse(
                task=task,
                report=report_record.report if report_record else None,
                validation=report_record.validation if report_record else None,
                execution=analysis.repository.get_task_execution(task_id, teacher_id),
            )
        except PermissionDeniedError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except StudentNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @application.post(
        "/api/v1/analysis/tasks/{task_id}/clarifications",
        response_model=CreateTaskResponse,
        status_code=status.HTTP_202_ACCEPTED,
    )
    def clarify_analysis_task(
        task_id: str,
        payload: ClarificationRequest,
        background_tasks: BackgroundTasks,
        teacher_id: Annotated[str, Depends(authenticated_teacher_id)],
        analysis: Annotated[AnalysisService, Depends(get_analysis_service)],
    ) -> CreateTaskResponse:
        try:
            context = analysis.clarify_task(task_id, teacher_id, payload.additional_information)
        except PermissionDeniedError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except StudentNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        background_tasks.add_task(analysis.run_task, task_id, context)
        return CreateTaskResponse(
            request_id=context.request_id,
            task_id=task_id,
            status=TaskStatus.RUNNING.value,
        )

    @application.post(
        "/api/v1/reports/{report_id}/confirmations",
        response_model=ConfirmationResponse,
    )
    def confirm_report(
        report_id: str,
        payload: ConfirmationRequest,
        background_tasks: BackgroundTasks,
        teacher_id: Annotated[str, Depends(authenticated_teacher_id)],
        analysis: Annotated[AnalysisService, Depends(get_analysis_service)],
    ) -> ConfirmationResponse:
        try:
            task_id = analysis.repository.report_task_id(report_id, teacher_id)
            _, parent_requested, context = analysis.repository.task_input(task_id, teacher_id)
            if payload.action == "regenerate":
                context = analysis.regenerate_task(task_id, teacher_id)
                background_tasks.add_task(analysis.run_task, task_id, context)
                return ConfirmationResponse(
                    report_id=report_id,
                    task_status=TaskStatus.RUNNING.value,
                )

            if payload.edited_report is not None:
                if payload.edited_report.report_id != report_id:
                    raise HTTPException(status_code=422, detail="报告 ID 与路径不一致")
                validation = analysis.validate_teacher_edit(
                    payload.edited_report,
                    context,
                    parent_summary_requested=parent_requested,
                )
            else:
                validation = analysis.repository.get_report(report_id, teacher_id).validation

            if payload.action == "save_edits":
                if payload.edited_report is None:
                    raise HTTPException(status_code=422, detail="保存修改必须提交 edited_report")
                analysis.repository.save_report_edit(payload.edited_report, validation, context)
                return ConfirmationResponse(
                    report_id=report_id,
                    task_status=TaskStatus.AWAITING_CONFIRMATION.value,
                    validation_passed=validation.passed,
                )
            if not validation.passed:
                raise HTTPException(status_code=422, detail="修改后的报告事实校验未通过")
            if payload.edited_report is not None:
                analysis.repository.save_report_edit(payload.edited_report, validation, context)
            analysis.repository.confirm_report(
                report_id, context, has_comments=bool(payload.comments)
            )
            return ConfirmationResponse(
                report_id=report_id,
                task_status=TaskStatus.COMPLETED.value,
                validation_passed=True,
            )
        except PermissionDeniedError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except StudentNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @application.get("/api/v1/reports/{report_id}/evidence", response_model=list[EvidenceItem])
    def get_report_evidence(
        report_id: str,
        teacher_id: Annotated[str, Depends(authenticated_teacher_id)],
        analysis: Annotated[AnalysisService, Depends(get_analysis_service)],
    ) -> list[EvidenceItem]:
        try:
            report = analysis.repository.get_report(report_id, teacher_id).report
        except PermissionDeniedError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except StudentNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        evidence = [
            EvidenceItem(claim=item.conclusion, evidence_ids=item.evidence_ids)
            for item in report.strengths + report.risks
        ]
        evidence.extend(
            EvidenceItem(
                claim=item.action,
                evidence_ids=item.evidence_ids,
                case_ids=item.reference_case_ids,
            )
            for item in report.recommended_actions
        )
        return evidence

    @application.get("/api/v1/reports/{report_id}/export")
    def export_report(
        report_id: str,
        teacher_id: Annotated[str, Depends(authenticated_teacher_id)],
        analysis: Annotated[AnalysisService, Depends(get_analysis_service)],
        export_format: Annotated[
            str, Query(alias="format", pattern="^(markdown|json)$")
        ] = "markdown",
    ) -> Response:
        try:
            record = analysis.repository.get_report(report_id, teacher_id)
        except PermissionDeniedError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except StudentNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        if record.confirmation_status != ConfirmationStatus.CONFIRMED:
            raise HTTPException(status_code=409, detail="报告经教师确认后才能导出")
        task_id = analysis.repository.report_task_id(report_id, teacher_id)
        _, _, context = analysis.repository.task_input(task_id, teacher_id)
        analysis.repository.audit(
            context,
            "report_exported",
            "report",
            report_id,
            {"format": export_format},
        )
        filename = f"{report_id}.{'md' if export_format == 'markdown' else 'json'}"
        headers = {"Content-Disposition": f'attachment; filename="{filename}"'}
        if export_format == "json":
            return Response(
                content=record.report.model_dump_json(indent=2),
                media_type="application/json",
                headers=headers,
            )
        return Response(
            content=report_to_markdown(record.report),
            media_type="text/markdown; charset=utf-8",
            headers=headers,
        )

    return application


app = create_app()


def run() -> None:
    settings = get_settings()
    uvicorn.run("futureedu_insight.api.main:app", host=settings.api_host, port=settings.api_port)
