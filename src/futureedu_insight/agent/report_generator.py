from __future__ import annotations

import json
from pathlib import Path
from typing import Protocol
from uuid import uuid4

import httpx
from pydantic import BaseModel, ConfigDict

from futureedu_insight.domain.errors import ModelServiceError
from futureedu_insight.domain.models import (
    ConfidenceLevel,
    EvidenceBasedConclusion,
    EvidenceReference,
    LearningDataBundle,
    LearningMetrics,
    LearningProfile,
    LearningReport,
    RecommendedAction,
    RetrievedCase,
    ScoreTrend,
)
from futureedu_insight.infrastructure.prompt_loader import load_prompt

EVIDENCE_QUALITY_SCORE = {
    ConfidenceLevel.HIGH: 1.0,
    ConfidenceLevel.MEDIUM: 0.7,
    ConfidenceLevel.LOW: 0.4,
}


class NarrativeEnhancement(BaseModel):
    """The only fields delegated to the language model."""

    model_config = ConfigDict(extra="ignore")

    overall_summary: str | None = None
    parent_communication_summary: str | None = None


def confidence_level(score: float) -> ConfidenceLevel:
    if score >= 0.8:
        return ConfidenceLevel.HIGH
    if score >= 0.55:
        return ConfidenceLevel.MEDIUM
    return ConfidenceLevel.LOW


def format_percentage(value: float | None) -> str:
    return "暂无可计算数据" if value is None else f"{value:.0%}"


def format_points(value: float | None) -> str:
    return "暂无可计算数据" if value is None else f"{value}个百分点"


class ReportGenerator(Protocol):
    def generate(
        self,
        data: LearningDataBundle,
        metrics: LearningMetrics,
        profile: LearningProfile,
        cases: list[RetrievedCase],
        *,
        include_parent_summary: bool,
    ) -> LearningReport: ...


class DeterministicReportGenerator:
    """Evidence-first report generator and offline fallback."""

    model_version = "deterministic-v1"

    def __init__(
        self,
        *,
        prompt_version: str,
        data_completeness_weight: float = 0.4,
        retrieval_score_weight: float = 0.3,
        evidence_quality_weight: float = 0.3,
    ) -> None:
        total = data_completeness_weight + retrieval_score_weight + evidence_quality_weight
        if abs(total - 1.0) > 1e-9:
            raise ValueError("置信度权重之和必须为1")
        self.prompt_version = prompt_version
        self.data_weight = data_completeness_weight
        self.retrieval_weight = retrieval_score_weight
        self.evidence_weight = evidence_quality_weight

    def _recommendations(
        self,
        profile: LearningProfile,
        cases: list[RetrievedCase],
    ) -> list[RecommendedAction]:
        if not cases:
            score = self.data_weight * profile.data_completeness
            return [
                RecommendedAction(
                    action="由教师结合课堂观察制定两周跟进计划，并记录同一指标的变化",
                    reason="当前没有达到相关性阈值的历史案例，建议以当前学生事实为依据进行小步验证",
                    evidence_ids=profile.evidence_record_ids,
                    reference_case_ids=[],
                    duration="2周",
                    observation_metric="当前主要薄弱知识点的得分率或作业正确率",
                    confidence_score=round(score, 4),
                    confidence_level=confidence_level(score),
                )
            ]

        recommendations: list[RecommendedAction] = []
        for result in cases[:2]:
            evidence_score = EVIDENCE_QUALITY_SCORE[result.case.evidence_quality]
            score = (
                self.data_weight * profile.data_completeness
                + self.retrieval_weight * result.relevance_score
                + self.evidence_weight * evidence_score
            )
            recommendations.append(
                RecommendedAction(
                    action=result.case.intervention,
                    reason="；".join(result.similarity_reasons),
                    evidence_ids=profile.evidence_record_ids,
                    reference_case_ids=[result.case.case_id],
                    duration=result.case.observation_period,
                    observation_metric=result.case.observed_metric,
                    confidence_score=round(score, 4),
                    confidence_level=confidence_level(score),
                )
            )
        return recommendations

    def generate(
        self,
        data: LearningDataBundle,
        metrics: LearningMetrics,
        profile: LearningProfile,
        cases: list[RetrievedCase],
        *,
        include_parent_summary: bool,
    ) -> LearningReport:
        trend_text = {
            ScoreTrend.DECLINING: "呈下降趋势",
            ScoreTrend.IMPROVING: "呈上升趋势",
            ScoreTrend.STABLE: "总体稳定",
            ScoreTrend.INSUFFICIENT: "数据不足，暂不能判断趋势",
        }[metrics.score_trend]

        score_analysis = None
        if data.scores:
            score_analysis = (
                f"分析期内共有{len(data.scores)}次成绩记录，标准化成绩{trend_text}。"
                f"首末次变化为{format_points(metrics.score_delta)}，最近一次与班级均分差为"
                f"{format_points(metrics.class_average_gap)}。"
            )
        homework_analysis = None
        if data.homework:
            homework_analysis = (
                f"作业提交率为{format_percentage(metrics.homework_submission_rate)}，"
                f"已提交作业平均正确率为{format_percentage(metrics.homework_accuracy_rate)}，"
                f"错题订正率为{format_percentage(metrics.correction_rate)}。"
            )
        attendance_analysis = None
        if data.attendance:
            attendance_analysis = f"分析期内到课率为{format_percentage(metrics.attendance_rate)}。"
        classroom_analysis = None
        if data.feedback:
            classroom_analysis = "课堂反馈标签包括：" + "、".join(metrics.classroom_tags) + "。"

        strengths: list[EvidenceBasedConclusion] = []
        risks: list[EvidenceBasedConclusion] = []
        if metrics.attendance_rate is not None and metrics.attendance_rate >= 0.95:
            strengths.append(
                EvidenceBasedConclusion(
                    conclusion="整体到课情况稳定",
                    evidence_ids=[record.record_id for record in data.attendance],
                )
            )
        if metrics.score_trend == ScoreTrend.DECLINING:
            decline_text = (
                "标准化成绩连续下降，需要关注近期失分原因"
                if len(data.scores) >= 3
                else "最近两次标准化成绩下降，需要关注近期失分原因"
            )
            risks.append(
                EvidenceBasedConclusion(
                    conclusion=decline_text,
                    evidence_ids=[record.record_id for record in data.scores],
                )
            )
        if metrics.correction_rate is not None and metrics.correction_rate < 0.5:
            risks.append(
                EvidenceBasedConclusion(
                    conclusion="错题订正率偏低，相同问题可能重复出现",
                    evidence_ids=[record.record_id for record in data.homework],
                )
            )

        evidence = [
            EvidenceReference(
                evidence_id=record_id,
                evidence_type="business_record",
                description="学情业务记录",
            )
            for record_id in metrics.evidence_record_ids
        ]
        evidence.extend(
            EvidenceReference(
                evidence_id=result.case.case_id,
                evidence_type="case",
                description=result.citation_text,
            )
            for result in cases
        )

        overall_summary = (
            f"{data.student.display_name}在{profile.period.start}至{profile.period.end}的{profile.subject}"
            f"学情数据完整度为{metrics.data_completeness:.0%}，成绩{trend_text}。"
        )
        if metrics.weak_knowledge_points:
            overall_summary += "主要薄弱点为" + "、".join(metrics.weak_knowledge_points) + "。"

        recommendations = self._recommendations(profile, cases)
        parent_summary = None
        if include_parent_summary:
            positive_parts: list[str] = []
            if metrics.attendance_rate is not None and metrics.attendance_rate >= 0.95:
                positive_parts.append("到课情况稳定")
            if (
                metrics.homework_submission_rate is not None
                and metrics.homework_submission_rate >= 0.9
            ):
                positive_parts.append("学习任务完成较为稳定")
            positive_text = "、".join(positive_parts) or "当前学习情况已完成阶段性记录"
            risk_text = "；".join(item.conclusion for item in risks) or "当前未发现明显异常"
            teacher_plan = recommendations[0].action.rstrip("。") + "。"
            family_action = (
                "建议家长关注错题是否按计划完成订正，鼓励孩子写清解题过程，"
                "并及时向教师反馈在家学习中的困难"
                if metrics.correction_rate is not None and metrics.correction_rate < 0.5
                else "建议家长保持日常关注与鼓励，并及时向教师反馈在家学习中的困难"
            )
            parent_summary = (
                f"本阶段{profile.subject}学习中，{data.student.display_name}{positive_text}。"
                f"当前需要关注：{risk_text}。接下来教师将{teacher_plan}"
                f"{family_action}。以上内容基于本阶段学习记录，后续将结合课堂表现持续调整。"
            )

        uncertainties: list[str] = []
        if metrics.data_completeness < 1:
            uncertainties.append("部分数据源缺失，结论仅覆盖现有记录")
        if not cases:
            uncertainties.append("暂无达到相关性阈值的历史案例")

        return LearningReport(
            report_id=f"REPORT-{uuid4().hex[:12].upper()}",
            student_id=data.student.student_id,
            student_display_name=data.student.display_name,
            subject=profile.subject,
            period=profile.period,
            overall_summary=overall_summary,
            data_completeness=metrics.data_completeness,
            score_analysis=score_analysis,
            homework_analysis=homework_analysis,
            attendance_analysis=attendance_analysis,
            classroom_analysis=classroom_analysis,
            weak_knowledge_points=metrics.weak_knowledge_points,
            strengths=strengths,
            risks=risks,
            retrieved_case_ids=[result.case.case_id for result in cases],
            recommended_actions=recommendations,
            parent_communication_summary=parent_summary,
            uncertainties=uncertainties,
            evidence=evidence,
            model_version=self.model_version,
            prompt_version=self.prompt_version,
        )


class OllamaReportGenerator:
    """Ollama structured-output gateway; deterministic mode remains the offline fallback."""

    def __init__(
        self,
        *,
        base_url: str,
        model_name: str,
        timeout_seconds: float,
        prompt_path: Path | str,
        max_transport_retries: int = 1,
        client: httpx.Client | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model_name = model_name
        self.timeout_seconds = timeout_seconds
        self.prompt = load_prompt(prompt_path)
        self.max_transport_retries = max_transport_retries
        self.client = client

    def _endpoint(self) -> str:
        return f"{self.base_url}/api/chat"

    def _provider_label(self) -> str:
        return "Ollama"

    def _headers(self) -> dict[str, str]:
        return {}

    def _payload(self, context: dict[str, object]) -> dict[str, object]:
        return {
            "model": self.model_name,
            "stream": False,
            "format": NarrativeEnhancement.model_json_schema(),
            "options": {"temperature": 0},
            "messages": self._messages(context),
        }

    def _messages(self, context: dict[str, object]) -> list[dict[str, str]]:
        return [
            {"role": "system", "content": self.prompt["system"]},
            {
                "role": "user",
                "content": self.prompt["instruction"]
                + "\n<structured_context>\n"
                + json.dumps(context, ensure_ascii=False)
                + "\n</structured_context>",
            },
        ]

    def _response_content(self, response: httpx.Response) -> str:
        return response.json()["message"]["content"]

    def _context(
        self,
        data: LearningDataBundle,
        metrics: LearningMetrics,
        profile: LearningProfile,
        cases: list[RetrievedCase],
        include_parent_summary: bool,
        grounded_draft: LearningReport,
    ) -> dict[str, object]:
        return {
            "student": data.student.model_dump(mode="json"),
            "learning_facts": {
                "metrics": metrics.model_dump(mode="json"),
                "profile": profile.model_dump(mode="json"),
                "score_record_count": len(data.scores),
                "homework_record_count": len(data.homework),
                "attendance_record_count": len(data.attendance),
                "feedback_record_count": len(data.feedback),
            },
            "retrieved_cases": [item.model_dump(mode="json") for item in cases],
            "allowed_business_evidence_ids": metrics.evidence_record_ids,
            "allowed_case_ids": [item.case.case_id for item in cases],
            "include_parent_summary": include_parent_summary,
            "grounded_draft": grounded_draft.model_dump(mode="json"),
            "report_id": f"REPORT-{uuid4().hex[:12].upper()}",
            "model_version": self.model_name,
            "prompt_version": self.prompt["version"],
        }

    def generate(
        self,
        data: LearningDataBundle,
        metrics: LearningMetrics,
        profile: LearningProfile,
        cases: list[RetrievedCase],
        *,
        include_parent_summary: bool,
    ) -> LearningReport:
        grounded_draft = DeterministicReportGenerator(
            prompt_version=self.prompt["version"]
        ).generate(
            data,
            metrics,
            profile,
            cases,
            include_parent_summary=include_parent_summary,
        )
        context = self._context(
            data,
            metrics,
            profile,
            cases,
            include_parent_summary,
            grounded_draft,
        )
        payload = self._payload(context)
        close_client = self.client is None
        client = self.client or httpx.Client(timeout=self.timeout_seconds)
        try:
            last_error: Exception | None = None
            for _ in range(self.max_transport_retries + 1):
                try:
                    response = client.post(
                        self._endpoint(), json=payload, headers=self._headers()
                    )
                    response.raise_for_status()
                    content = self._response_content(response)
                    NarrativeEnhancement.model_validate_json(content)
                    # The model may improve prose, but calculated facts, evidence
                    # IDs, case-backed actions and confidence values remain owned
                    # by deterministic code. This makes a local small model useful
                    # without allowing it to silently erase or mutate evidence.
                    return grounded_draft.model_copy(
                        update={
                            "report_id": context["report_id"],
                            "model_version": self.model_name,
                            "prompt_version": self.prompt["version"],
                        }
                    )
                except (httpx.HTTPError, IndexError, KeyError, TypeError, ValueError) as exc:
                    last_error = exc
            raise ModelServiceError(
                f"{self._provider_label()} 结构化报告生成失败: {last_error}"
            )
        finally:
            if close_client:
                client.close()


class OpenAICompatibleReportGenerator(OllamaReportGenerator):
    """Gateway for OpenAI-compatible ``/chat/completions`` model APIs."""

    def __init__(self, *, api_key: str, **kwargs) -> None:
        if not api_key.strip():
            raise ValueError("OpenAI 兼容模型模式必须配置 APP_MODEL_API_KEY")
        super().__init__(**kwargs)
        self.api_key = api_key.strip()

    def _endpoint(self) -> str:
        return f"{self.base_url}/chat/completions"

    def _provider_label(self) -> str:
        return "OpenAI 兼容模型"

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}"}

    def _payload(self, context: dict[str, object]) -> dict[str, object]:
        return {
            "model": self.model_name,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": self._messages(context),
        }

    def _response_content(self, response: httpx.Response) -> str:
        return response.json()["choices"][0]["message"]["content"]
