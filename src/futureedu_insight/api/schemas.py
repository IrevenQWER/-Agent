from __future__ import annotations

from typing import Literal

from pydantic import Field

from futureedu_insight.domain.models import (
    LearningReport,
    ReportValidationResult,
    StrictModel,
    TaskRecord,
)


class CreateTaskRequest(StrictModel):
    query: str = Field(min_length=1, max_length=1000)
    session_id: str = Field(min_length=1, max_length=128)
    include_parent_summary: bool = False


class CreateTaskResponse(StrictModel):
    request_id: str
    task_id: str
    status: str


class TaskResponse(StrictModel):
    task: TaskRecord
    report: LearningReport | None = None
    validation: ReportValidationResult | None = None
    execution: dict | None = None


class ClarificationRequest(StrictModel):
    additional_information: str = Field(min_length=1, max_length=500)


class ConfirmationRequest(StrictModel):
    action: Literal["confirm", "save_edits", "regenerate"]
    comments: str | None = Field(default=None, max_length=1000)
    edited_report: LearningReport | None = None


class ConfirmationResponse(StrictModel):
    report_id: str
    task_status: str
    validation_passed: bool | None = None


class EvidenceItem(StrictModel):
    claim: str
    evidence_ids: list[str]
    case_ids: list[str] = Field(default_factory=list)
