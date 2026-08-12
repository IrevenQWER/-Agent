import json
from datetime import date
from pathlib import Path

import httpx

from futureedu_insight.agent.report_generator import (
    DeterministicReportGenerator,
    OllamaReportGenerator,
    OpenAICompatibleReportGenerator,
)
from futureedu_insight.domain.models import (
    DateRange,
    LearningDataBundle,
    LearningMetrics,
    LearningProfile,
    ScoreTrend,
    StudentProfile,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_ollama_gateway_requests_schema_constrained_output() -> None:
    period = DateRange(start=date(2026, 3, 1), end=date(2026, 3, 31))
    student = StudentProfile(
        student_id="S1001",
        display_name="张晨",
        grade="八年级",
        class_id="C1",
        class_name="八年级一班",
        campus_id="CAMPUS1",
    )
    data = LearningDataBundle(student=student)
    metrics = LearningMetrics(data_completeness=0)
    profile = LearningProfile(
        student_id=student.student_id,
        grade=student.grade,
        subject="数学",
        period=period,
        rules_version="1.0.0",
        score_trend=ScoreTrend.INSUFFICIENT,
        data_completeness=0,
    )
    expected = DeterministicReportGenerator(prompt_version="1.0.0").generate(
        data, metrics, profile, [], include_parent_summary=False
    )
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={"message": {"content": expected.model_dump_json()}},
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    generator = OllamaReportGenerator(
        base_url="http://ollama.test",
        model_name="qwen-test",
        timeout_seconds=1,
        prompt_path=PROJECT_ROOT / "prompts" / "report_generation.yaml",
        client=client,
    )

    actual = generator.generate(data, metrics, profile, [], include_parent_summary=False)

    assert actual.overall_summary == expected.overall_summary
    assert actual.student_id == "S1001"
    assert actual.model_version == "qwen-test"
    assert actual.report_id != expected.report_id
    assert captured["format"]["title"] == "NarrativeEnhancement"
    assert "<structured_context>" in captured["messages"][1]["content"]
    assert "grounded_draft" in captured["messages"][1]["content"]
    assert captured["options"]["temperature"] == 0
    client.close()


def test_openai_compatible_gateway_uses_api_key_and_chat_completions() -> None:
    period = DateRange(start=date(2026, 3, 1), end=date(2026, 3, 31))
    student = StudentProfile(
        student_id="S1001",
        display_name="张晨",
        grade="八年级",
        class_id="C1",
        class_name="八年级一班",
        campus_id="CAMPUS1",
    )
    data = LearningDataBundle(student=student)
    metrics = LearningMetrics(data_completeness=0)
    profile = LearningProfile(
        student_id=student.student_id,
        grade=student.grade,
        subject="数学",
        period=period,
        rules_version="1.0.0",
        score_trend=ScoreTrend.INSUFFICIENT,
        data_completeness=0,
    )
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["authorization"] = request.headers.get("authorization")
        captured["payload"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "overall_summary": "阶段学习情况已完成分析",
                                    "parent_communication_summary": None,
                                },
                                ensure_ascii=False,
                            )
                        }
                    }
                ]
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    generator = OpenAICompatibleReportGenerator(
        base_url="https://api.example.com/v1",
        model_name="example-chat",
        api_key="secret-test-key",
        timeout_seconds=1,
        prompt_path=PROJECT_ROOT / "prompts" / "report_generation.yaml",
        client=client,
    )

    actual = generator.generate(data, metrics, profile, [], include_parent_summary=False)

    assert captured["url"] == "https://api.example.com/v1/chat/completions"
    assert captured["authorization"] == "Bearer secret-test-key"
    assert captured["payload"]["response_format"] == {"type": "json_object"}
    assert actual.model_version == "example-chat"
    assert "secret-test-key" not in actual.model_dump_json()
    client.close()


def test_ollama_gateway_repairs_sparse_model_output_with_grounded_scaffold() -> None:
    period = DateRange(start=date(2026, 3, 1), end=date(2026, 3, 31))
    student = StudentProfile(
        student_id="S1001",
        display_name="张晨",
        grade="八年级",
        class_id="C1",
        class_name="八年级一班",
        campus_id="CAMPUS1",
    )
    data = LearningDataBundle(student=student)
    metrics = LearningMetrics(
        data_completeness=0,
        weak_knowledge_points=["一次函数"],
    )
    profile = LearningProfile(
        student_id=student.student_id,
        grade=student.grade,
        subject="数学",
        period=period,
        rules_version="1.0.0",
        score_trend=ScoreTrend.INSUFFICIENT,
        data_completeness=0,
        weak_knowledge_points=["一次函数"],
    )
    sparse = DeterministicReportGenerator(prompt_version="1.0.0").generate(
        data, LearningMetrics(data_completeness=0), profile, [], include_parent_summary=False
    ).model_copy(
        update={
            "overall_summary": "模型擅自加入了99%的数字",
            "weak_knowledge_points": [],
            "evidence": [],
        }
    )

    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200, json={"message": {"content": sparse.model_dump_json()}}
            )
        )
    )
    generator = OllamaReportGenerator(
        base_url="http://ollama.test",
        model_name="qwen-test",
        timeout_seconds=1,
        prompt_path=PROJECT_ROOT / "prompts" / "report_generation.yaml",
        client=client,
    )

    actual = generator.generate(data, metrics, profile, [], include_parent_summary=False)

    assert actual.weak_knowledge_points == ["一次函数"]
    assert "99" not in actual.overall_summary
    assert actual.model_version == "qwen-test"
    client.close()


def test_ollama_gateway_does_not_publish_unsupported_nonnumeric_prose() -> None:
    period = DateRange(start=date(2026, 3, 1), end=date(2026, 3, 31))
    student = StudentProfile(
        student_id="S1001",
        display_name="张晨",
        grade="八年级",
        class_id="C1",
        class_name="八年级一班",
        campus_id="CAMPUS1",
    )
    data = LearningDataBundle(student=student)
    metrics = LearningMetrics(data_completeness=0)
    profile = LearningProfile(
        student_id=student.student_id,
        grade=student.grade,
        subject="数学",
        period=period,
        rules_version="1.0.0",
        score_trend=ScoreTrend.INSUFFICIENT,
        data_completeness=0,
    )
    candidate = json.dumps(
        {
            "overall_summary": "学生可能因为计算能力不足导致成绩持续下降",
            "parent_communication_summary": "建议家长针对学生能力问题加强训练",
        },
        ensure_ascii=False,
    )
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json={"message": {"content": candidate}})
        )
    )
    generator = OllamaReportGenerator(
        base_url="http://ollama.test",
        model_name="qwen-test",
        timeout_seconds=1,
        prompt_path=PROJECT_ROOT / "prompts" / "report_generation.yaml",
        client=client,
    )

    actual = generator.generate(data, metrics, profile, [], include_parent_summary=True)

    assert "计算能力不足" not in actual.overall_summary
    assert "能力问题" not in (actual.parent_communication_summary or "")
    assert actual.parent_communication_summary is not None
    client.close()
