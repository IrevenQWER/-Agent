from __future__ import annotations

import calendar
import re
from datetime import date, timedelta

from futureedu_insight.domain.models import (
    AnalysisFocus,
    DateRange,
    ParsedAnalysisRequest,
)

SUBJECTS = ("数学", "物理", "化学", "语文", "英语")
CHINESE_MONTHS = {
    "一": 1,
    "二": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "十": 10,
    "十一": 11,
    "十二": 12,
}


class DeterministicRequestParser:
    """Offline parser used for tests and graceful model degradation."""

    def __init__(self, *, today: date | None = None) -> None:
        self.today = today or date.today()

    @staticmethod
    def _student_identifier(query: str) -> str | None:
        id_match = re.search(r"\bS\d{4,}\b", query, flags=re.IGNORECASE)
        if id_match:
            return id_match.group(0).upper()
        subject_boundary = "|".join(SUBJECTS)
        student_match = re.search(
            rf"学生\s*([\u4e00-\u9fff]{{2,4}}?)(?=的|20\d{{2}}年|\d{{1,2}}月|"
            rf"{subject_boundary}|学情|学习|成绩|作业|报告|[,，。；;\s]|$)",
            query,
        )
        if student_match:
            return student_match.group(1)

        classmate_match = re.search(r"([\u4e00-\u9fff]{2,8})同学", query)
        if classmate_match:
            candidate = classmate_match.group(1)
            for prefix in ("帮我分析", "请分析", "分析", "请查看", "查看"):
                if candidate.startswith(prefix):
                    candidate = candidate.removeprefix(prefix)
                    break
            if 2 <= len(candidate) <= 4:
                return candidate
        return None

    @staticmethod
    def _class_context(query: str) -> str | None:
        match = re.search(
            r"([一二三四五六七八九十0-9]{1,3}年级[\u4e00-\u9fffA-Za-z0-9]{0,8}?班)",
            query,
        )
        return match.group(1) if match else None

    def _period(self, query: str) -> DateRange | None:
        numeric = re.search(r"(20\d{2})年\s*(1[0-2]|0?[1-9])月", query)
        if numeric:
            year, month = int(numeric.group(1)), int(numeric.group(2))
            return DateRange(
                start=date(year, month, 1),
                end=date(year, month, calendar.monthrange(year, month)[1]),
            )

        chinese = re.search(r"([一二三四五六七八九十]{1,2})月份?", query)
        if chinese and chinese.group(1) in CHINESE_MONTHS:
            month = CHINESE_MONTHS[chinese.group(1)]
            year = self.today.year
            return DateRange(
                start=date(year, month, 1),
                end=date(year, month, calendar.monthrange(year, month)[1]),
            )

        if "今年春季前四周" in query:
            start = date(self.today.year, 3, 1)
            return DateRange(start=start, end=start + timedelta(days=27))
        return None

    @staticmethod
    def _focus(query: str) -> list[AnalysisFocus]:
        focus: list[AnalysisFocus] = []
        mappings = (
            (AnalysisFocus.SCORE, ("成绩", "考试", "分数", "排名")),
            (AnalysisFocus.HOMEWORK, ("作业", "订正")),
            (AnalysisFocus.ATTENDANCE, ("考勤", "到课", "迟到", "缺勤")),
            (AnalysisFocus.CLASSROOM, ("课堂", "表现", "反馈")),
        )
        for item, keywords in mappings:
            if any(keyword in query for keyword in keywords):
                focus.append(item)
        return focus or [AnalysisFocus.COMPREHENSIVE]

    def parse(
        self,
        query: str,
        *,
        include_parent_summary: bool = False,
    ) -> ParsedAnalysisRequest:
        normalized = query.strip()
        if not normalized:
            return ParsedAnalysisRequest(
                supported=False,
                unsupported_reason="请求不能为空",
            )
        if not any(keyword in normalized for keyword in ("学情", "学习", "报告", "成绩", "作业")):
            return ParsedAnalysisRequest(
                supported=False,
                unsupported_reason="当前系统只支持单个学生的学情分析",
            )

        student_identifier = self._student_identifier(normalized)
        subject = next((subject for subject in SUBJECTS if subject in normalized), None)
        period = self._period(normalized)
        missing_fields: list[str] = []
        if not student_identifier:
            missing_fields.append("student_identifier")
        if not subject:
            missing_fields.append("subject")
        if not period:
            missing_fields.append("period")

        return ParsedAnalysisRequest(
            supported=True,
            needs_clarification=bool(missing_fields),
            missing_fields=missing_fields,
            student_identifier=student_identifier,
            class_context=self._class_context(normalized),
            subject=subject,
            period=period,
            analysis_focus=self._focus(normalized),
            include_parent_summary=include_parent_summary,
        )
