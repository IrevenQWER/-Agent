from __future__ import annotations

from futureedu_insight.domain.models import LearningReport


def report_to_markdown(report: LearningReport) -> str:
    lines = [
        f"# {report.student_display_name} {report.subject}学情报告",
        "",
        f"- 分析周期：{report.period.start} 至 {report.period.end}",
        f"- 数据完整度：{report.data_completeness:.0%}",
        f"- 报告编号：{report.report_id}",
        "",
        "## 总体结论",
        "",
        report.overall_summary,
    ]
    sections = (
        ("成绩分析", report.score_analysis),
        ("作业分析", report.homework_analysis),
        ("考勤分析", report.attendance_analysis),
        ("课堂反馈", report.classroom_analysis),
    )
    for title, content in sections:
        if content:
            lines.extend(("", f"## {title}", "", content))
    if report.risks:
        lines.extend(("", "## 需要关注", ""))
        lines.extend(
            f"- {item.conclusion}（证据：{', '.join(item.evidence_ids)}）" for item in report.risks
        )
    if report.recommended_actions:
        lines.extend(("", "## 建议行动", ""))
        for index, action in enumerate(report.recommended_actions, start=1):
            lines.extend(
                (
                    f"### {index}. {action.action}",
                    "",
                    f"- 原因：{action.reason}",
                    f"- 周期：{action.duration}",
                    f"- 观察指标：{action.observation_metric}",
                    f"- 案例引用：{', '.join(action.reference_case_ids) or '无'}",
                )
            )
    if report.parent_communication_summary:
        lines.extend(("", "## 家长反馈摘要", "", report.parent_communication_summary))
    lines.extend(("", "## 证据清单", ""))
    lines.extend(f"- `{item.evidence_id}`：{item.description}" for item in report.evidence)
    if report.uncertainties:
        lines.extend(("", "## 不确定性", ""))
        lines.extend(f"- {item}" for item in report.uncertainties)
    return "\n".join(lines) + "\n"
