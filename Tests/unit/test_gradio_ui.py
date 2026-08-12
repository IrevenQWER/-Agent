from futureedu_insight.ui.gradio_app import (
    _display_body,
    _parent_feedback_text,
    _teacher_report_markdown,
)


def test_display_body_surfaces_model_failure() -> None:
    body = {
        "task": {
            "status": "failed",
            "error_code": "UNEXPECTED_ERROR",
            "error_message": "Ollama 结构化报告生成失败: timed out",
        },
        "report": None,
    }

    displayed = _display_body(body)

    assert displayed["ui_message"] == "生成失败：Ollama 结构化报告生成失败: timed out"


def test_display_body_explains_successful_draft() -> None:
    displayed = _display_body({"task": {"status": "awaiting_confirmation"}})

    assert "点击“确认报告”" in displayed["ui_message"]


def test_teacher_report_markdown_prioritizes_readable_summary() -> None:
    body = {
        "task": {"status": "awaiting_confirmation"},
        "report": {
            "report_id": "REPORT-1",
            "student_id": "S1001",
            "student_display_name": "张晨",
            "subject": "数学",
            "period": {"start": "2026-03-01", "end": "2026-03-31"},
            "overall_summary": "成绩呈下降趋势，需要关注错题订正。",
            "data_completeness": 1,
            "weak_knowledge_points": ["一次函数应用题"],
            "strengths": [],
            "risks": [
                {"conclusion": "错题订正率偏低", "evidence_ids": ["HW-1"]}
            ],
            "retrieved_case_ids": [],
            "recommended_actions": [],
            "uncertainties": [],
            "evidence": [],
            "model_version": "qwen3:8b",
            "prompt_version": "1.0.0",
        },
        "validation": {"passed": True},
        "execution": {
            "metrics": {"evidence_record_ids": ["SCORE-1", "HW-1", "AT-1"]},
            "retrieved_cases": [],
        },
    }

    rendered = _teacher_report_markdown(body)

    assert "# 张晨 · 数学学情报告" in rendered
    assert "✅ 已通过事实一致性校验" in rendered
    assert "成绩1条、作业1条、考勤1条、课堂反馈0条" in rendered
    assert "## 需要关注" in rendered
    assert "错题订正率偏低" in rendered


def test_parent_feedback_is_extracted_for_one_click_copy() -> None:
    body = {"report": {"parent_communication_summary": "请家长关注错题订正。"}}

    assert _parent_feedback_text(body) == "请家长关注错题订正。"


def test_parent_feedback_explains_when_not_requested() -> None:
    assert "同时生成家长反馈摘要" in _parent_feedback_text(
        {"task": {"status": "awaiting_confirmation"}, "report": {}}
    )
