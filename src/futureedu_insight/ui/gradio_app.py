from __future__ import annotations

import json
import os
import time
from html import escape
from typing import Any

import httpx

from futureedu_insight.domain.models import LearningReport

API_BASE_URL = os.getenv("FUTUREEDU_API_URL", "http://127.0.0.1:8000")
UI_WAIT_SECONDS = float(os.getenv("FUTUREEDU_UI_WAIT_SECONDS", "300"))
UI_POLL_INTERVAL_SECONDS = float(os.getenv("FUTUREEDU_UI_POLL_INTERVAL_SECONDS", "1"))


def _headers(teacher_id: str) -> dict[str, str]:
    return {"X-Teacher-ID": teacher_id.strip()}


def _display_body(body: dict[str, Any]) -> dict[str, Any]:
    task = body.get("task") or {}
    status = task.get("status", "unknown")
    if status == "failed":
        message = f"生成失败：{task.get('error_message') or task.get('error_code') or '未知错误'}"
    elif status == "awaiting_confirmation":
        message = "报告已生成并通过事实校验，请检查后点击“确认报告”。"
    elif status == "completed":
        message = "报告已经教师确认。"
    elif status == "needs_clarification":
        message = "信息不足，请根据 clarification 补充学生、学科或时间范围。"
    else:
        message = "本地模型仍在生成，请稍候。"
    return {"ui_message": message, **body}


def _teacher_report_markdown(body: dict[str, Any]) -> str:
    task = body.get("task") or {}
    status = task.get("status", "unknown")
    report_payload = body.get("report")
    if status == "failed":
        error = escape(task.get("error_message") or task.get("error_code") or "未知错误")
        return f"## ❌ 报告生成失败\n\n{error}\n"
    if status == "needs_clarification":
        clarification = escape(str(task.get("clarification") or "请补充学生、学科或时间范围"))
        return f"## ℹ️ 需要补充信息\n\n{clarification}\n"
    if not report_payload:
        return "## ⏳ 正在生成学情报告\n\n本地模型正在分析多源学情数据，请稍候。\n"

    report = LearningReport.model_validate(report_payload)
    validation_passed = bool((body.get("validation") or {}).get("passed"))
    execution = body.get("execution") or {}
    retrieved_cases = execution.get("retrieved_cases") or []
    evidence_ids = (execution.get("metrics") or {}).get("evidence_record_ids") or []
    source_counts = {
        "成绩": sum(item.startswith("SCORE-") for item in evidence_ids),
        "作业": sum(item.startswith("HW-") for item in evidence_ids),
        "考勤": sum(item.startswith("AT-") for item in evidence_ids),
        "课堂反馈": sum(item.startswith("FB-") for item in evidence_ids),
    }
    verified = "✅ 已通过事实一致性校验" if validation_passed else "⚠️ 尚未通过事实校验"
    lines = [
        f"# {escape(report.student_display_name)} · {escape(report.subject)}学情报告",
        "",
        f"> {verified}，当前为教师确认前的报告草稿。",
        "",
        "## 报告概览",
        "",
        f"- **分析周期**：{report.period.start} 至 {report.period.end}",
        f"- **数据完整度**：{report.data_completeness:.0%}",
        "- **数据来源**："
        + "、".join(f"{name}{count}条" for name, count in source_counts.items()),
        f"- **参考历史案例**：{len(retrieved_cases)} 个",
        "",
        "## 总体结论",
        "",
        escape(report.overall_summary),
    ]
    sections = (
        ("成绩表现", report.score_analysis),
        ("作业表现", report.homework_analysis),
        ("考勤情况", report.attendance_analysis),
        ("课堂表现", report.classroom_analysis),
    )
    for title, content in sections:
        if content:
            lines.extend(("", f"### {title}", "", escape(content)))
    if report.weak_knowledge_points:
        lines.extend(
            (
                "",
                "## 重点薄弱知识点",
                "",
                *[f"- {escape(item)}" for item in report.weak_knowledge_points],
            )
        )
    if report.strengths:
        lines.extend(("", "## 当前优势", ""))
        lines.extend(f"- {escape(item.conclusion)}" for item in report.strengths)
    if report.risks:
        lines.extend(("", "## 需要关注", ""))
        lines.extend(f"- {escape(item.conclusion)}" for item in report.risks)
    if report.recommended_actions:
        lines.extend(("", "## 建议的教学行动", ""))
        for index, action in enumerate(report.recommended_actions, start=1):
            confidence = {"high": "高", "medium": "中", "low": "低"}[
                action.confidence_level.value
            ]
            lines.extend(
                (
                    f"### 建议 {index}：{escape(action.action)}",
                    "",
                    f"- **建议依据**：{escape(action.reason)}",
                    f"- **执行周期**：{escape(action.duration)}",
                    f"- **观察指标**：{escape(action.observation_metric)}",
                    f"- **参考可信度**：{confidence}（{action.confidence_score:.0%}）",
                    "",
                )
            )
    if retrieved_cases:
        lines.extend(("## 历史案例参考依据", ""))
        for item in retrieved_cases:
            reasons = "、".join(escape(reason) for reason in item.get("similarity_reasons", []))
            lines.append(f"- **{escape(item.get('case_id', '案例'))}**：{reasons}")
        lines.extend(("", "> 历史案例仅作为教学建议参考，不代表当前学生一定产生相同结果。"))
    if report.parent_communication_summary:
        lines.extend(
            ("", "## 家长反馈摘要", "", escape(report.parent_communication_summary))
        )
    if report.uncertainties:
        lines.extend(("", "## 数据限制与不确定性", ""))
        lines.extend(f"- {escape(item)}" for item in report.uncertainties)
    lines.extend(
        (
            "",
            "---",
            "请任课教师或班主任结合真实课堂观察审核以上内容，确认后再用于教学跟进。",
        )
    )
    return "\n".join(lines) + "\n"


def _parent_feedback_text(body: dict[str, Any]) -> str:
    report_payload = body.get("report") or {}
    summary = report_payload.get("parent_communication_summary")
    if summary:
        return str(summary)
    if (body.get("task") or {}).get("status") == "failed":
        return "报告生成失败，暂时无法形成家长反馈摘要。"
    return "本次未生成家长反馈摘要；如需生成，请勾选“同时生成家长反馈摘要”。"


def submit_analysis(
    teacher_id: str,
    query: str,
    include_parent_summary: bool,
) -> tuple[str, str, str, dict[str, Any], str, str]:
    with httpx.Client(base_url=API_BASE_URL, timeout=30) as client:
        response = client.post(
            "/api/v1/analysis/tasks",
            headers=_headers(teacher_id),
            json={
                "query": query,
                "session_id": f"GRADIO-{int(time.time())}",
                "include_parent_summary": include_parent_summary,
            },
        )
        response.raise_for_status()
        task_id = response.json()["task_id"]
        body: dict[str, Any] = {}
        poll_count = max(1, int(UI_WAIT_SECONDS / UI_POLL_INTERVAL_SECONDS))
        for _ in range(poll_count):
            task = client.get(f"/api/v1/analysis/tasks/{task_id}", headers=_headers(teacher_id))
            task.raise_for_status()
            body = task.json()
            if body["task"]["status"] != "running":
                report_id = (body.get("report") or {}).get("report_id", "")
                editable = json.dumps(body.get("report") or {}, ensure_ascii=False, indent=2)
                return (
                    _teacher_report_markdown(body),
                    _parent_feedback_text(body),
                    task_id,
                    _display_body(body),
                    report_id,
                    editable,
                )
            time.sleep(UI_POLL_INTERVAL_SECONDS)
    return (
        _teacher_report_markdown(body),
        _parent_feedback_text(body),
        task_id,
        _display_body(body),
        "",
        "",
    )


def review_analysis(
    teacher_id: str,
    report_id: str,
    edited_report_json: str,
    action: str,
) -> tuple[str, dict[str, Any]]:
    edited_report = json.loads(edited_report_json) if edited_report_json.strip() else None
    with httpx.Client(base_url=API_BASE_URL, timeout=30) as client:
        response = client.post(
            f"/api/v1/reports/{report_id}/confirmations",
            headers=_headers(teacher_id),
            json={
                "action": action,
                "comments": "教师工作台操作",
                "edited_report": edited_report,
            },
        )
        response.raise_for_status()
        payload = response.json()
        if action == "confirm":
            message = "## ✅ 报告已确认\n\n该报告已经完成教师审核，可以用于后续教学跟进与导出。"
        else:
            message = "## ✅ 修改已保存\n\n系统已重新执行事实校验，请核对技术详情中的校验结果。"
        return message, payload


def build_app():
    try:
        import gradio as gr
    except ImportError as exc:
        raise RuntimeError("请先安装 UI 依赖：pip install -e '.[ui]'") from exc

    with gr.Blocks(title="学生学情分析 Agent") as demo:
        gr.Markdown("# 学生学情分析 Agent\n面向一线教师的单学生、多源、证据可追溯分析工作台")
        teacher_id = gr.Textbox(value="T1001", label="教师编号（Demo 认证上下文）")
        query = gr.Textbox(
            value="生成学生 S1001 2026年3月数学学情报告，重点看成绩和作业",
            label="分析请求",
            lines=3,
        )
        parent_summary = gr.Checkbox(label="同时生成家长反馈摘要", value=True)
        submit = gr.Button("生成报告", variant="primary")
        report_view = gr.Markdown("提交分析请求后，这里将显示适合教师阅读的学情报告。")
        parent_feedback = gr.Textbox(
            value="生成后，这里将显示可直接复制的家长反馈摘要。",
            label="家长反馈摘要",
            info="请教师结合实际情况审核或调整后再反馈给家长，系统不会自动发送。",
            lines=7,
            max_lines=12,
            interactive=False,
            buttons=["copy"],
        )
        confirm = gr.Button("审核通过并生成正式报告", variant="primary")
        review_message = gr.Markdown()
        with gr.Accordion("技术详情与报告编辑（开发调试）", open=False):
            task_id = gr.Textbox(label="任务 ID")
            result = gr.JSON(label="任务、校验与执行详情")
            report_id = gr.Textbox(label="待确认报告 ID")
            edited_report = gr.Code(
                label="可编辑结构化报告（保存时会重新执行事实校验）",
                language="json",
            )
            save = gr.Button("保存结构化修改")
            review_result = gr.JSON(label="审核接口详情")

        submit.click(
            submit_analysis,
            inputs=[teacher_id, query, parent_summary],
            outputs=[report_view, parent_feedback, task_id, result, report_id, edited_report],
        )
        save.click(
            lambda teacher, report, content: review_analysis(
                teacher, report, content, "save_edits"
            ),
            inputs=[teacher_id, report_id, edited_report],
            outputs=[review_message, review_result],
        )
        confirm.click(
            lambda teacher, report, content: review_analysis(teacher, report, content, "confirm"),
            inputs=[teacher_id, report_id, edited_report],
            outputs=[review_message, review_result],
        )
    return demo


def main() -> None:
    build_app().launch(
        server_name=os.getenv("FUTUREEDU_UI_HOST", "127.0.0.1"),
        server_port=int(os.getenv("FUTUREEDU_UI_PORT", "7860")),
    )


if __name__ == "__main__":
    main()
