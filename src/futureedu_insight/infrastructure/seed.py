from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from futureedu_insight.config import get_settings

SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS students (
    student_id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    grade TEXT NOT NULL,
    class_id TEXT NOT NULL,
    class_name TEXT NOT NULL,
    campus_id TEXT NOT NULL,
    enrollment_status TEXT NOT NULL CHECK (enrollment_status IN ('active', 'inactive'))
);

CREATE TABLE IF NOT EXISTS teacher_student_access (
    teacher_id TEXT NOT NULL,
    student_id TEXT NOT NULL REFERENCES students(student_id),
    PRIMARY KEY (teacher_id, student_id)
);

CREATE TABLE IF NOT EXISTS scores (
    record_id TEXT PRIMARY KEY,
    student_id TEXT NOT NULL REFERENCES students(student_id),
    exam_id TEXT NOT NULL,
    exam_name TEXT NOT NULL,
    subject TEXT NOT NULL,
    score REAL NOT NULL,
    full_score REAL NOT NULL,
    class_average REAL NOT NULL,
    rank INTEGER NOT NULL,
    participant_count INTEGER NOT NULL,
    exam_date TEXT NOT NULL,
    knowledge_scores TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS homework_records (
    record_id TEXT PRIMARY KEY,
    student_id TEXT NOT NULL REFERENCES students(student_id),
    subject TEXT NOT NULL,
    homework_date TEXT NOT NULL,
    submitted INTEGER NOT NULL,
    accuracy_rate REAL,
    corrected INTEGER,
    knowledge_tags TEXT NOT NULL,
    teacher_comment TEXT
);

CREATE TABLE IF NOT EXISTS attendance_records (
    record_id TEXT PRIMARY KEY,
    student_id TEXT NOT NULL REFERENCES students(student_id),
    course_id TEXT NOT NULL,
    lesson_date TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('present', 'late', 'leave', 'absent')),
    late_minutes INTEGER NOT NULL DEFAULT 0,
    leave_type TEXT
);

CREATE TABLE IF NOT EXISTS classroom_feedback (
    record_id TEXT PRIMARY KEY,
    student_id TEXT NOT NULL REFERENCES students(student_id),
    teacher_id TEXT NOT NULL,
    course_id TEXT NOT NULL,
    subject TEXT NOT NULL,
    feedback_date TEXT NOT NULL,
    performance_tags TEXT NOT NULL,
    feedback_text TEXT
);

CREATE INDEX IF NOT EXISTS idx_scores_lookup
ON scores(student_id, subject, exam_date);
CREATE INDEX IF NOT EXISTS idx_homework_lookup
ON homework_records(student_id, subject, homework_date);

CREATE TABLE IF NOT EXISTS analysis_tasks (
    task_id TEXT PRIMARY KEY,
    request_id TEXT NOT NULL UNIQUE,
    session_id TEXT NOT NULL,
    teacher_id TEXT NOT NULL,
    query TEXT NOT NULL,
    include_parent_summary INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL,
    report_id TEXT,
    clarification_json TEXT,
    execution_json TEXT,
    error_code TEXT,
    error_message TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_analysis_tasks_owner
ON analysis_tasks(teacher_id, task_id);

CREATE TABLE IF NOT EXISTS learning_reports (
    report_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL UNIQUE,
    teacher_id TEXT NOT NULL,
    report_json TEXT NOT NULL,
    validation_json TEXT NOT NULL,
    confirmation_status TEXT NOT NULL,
    confirmed_by TEXT,
    confirmed_at TEXT,
    teacher_edits_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(task_id) REFERENCES analysis_tasks(task_id)
);

CREATE INDEX IF NOT EXISTS idx_learning_reports_owner
ON learning_reports(teacher_id, report_id);

CREATE TABLE IF NOT EXISTS audit_events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id TEXT NOT NULL,
    teacher_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    resource_type TEXT NOT NULL,
    resource_id TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_attendance_lookup
ON attendance_records(student_id, lesson_date);
CREATE INDEX IF NOT EXISTS idx_feedback_lookup
ON classroom_feedback(student_id, subject, feedback_date);
"""


STUDENTS = [
    ("S1001", "张晨", "八年级", "C-MATH-801", "八年级数学一班", "CAMPUS-YUEYANG", "active"),
    ("S1002", "李悦", "八年级", "C-MATH-801", "八年级数学一班", "CAMPUS-YUEYANG", "active"),
    ("S2001", "张晨", "八年级", "C-MATH-802", "八年级数学二班", "CAMPUS-XIANGTAN", "active"),
]

ACCESS = [
    ("T1001", "S1001"),
    ("T1001", "S1002"),
    ("T2001", "S2001"),
]

SCORES = [
    (
        "SCORE-1001-01",
        "S1001",
        "EXAM-01",
        "春季入学测",
        "数学",
        89,
        100,
        82,
        8,
        35,
        "2026-03-05",
        {"一次函数应用题": 0.72, "几何辅助线": 0.68, "基础运算": 0.94},
    ),
    (
        "SCORE-1001-02",
        "S1001",
        "EXAM-02",
        "春季第一次月考",
        "数学",
        85,
        100,
        81,
        12,
        35,
        "2026-03-28",
        {"一次函数应用题": 0.61, "几何辅助线": 0.62, "基础运算": 0.92},
    ),
    (
        "SCORE-1001-03",
        "S1001",
        "EXAM-03",
        "春季第二次月考",
        "数学",
        81,
        100,
        83,
        18,
        35,
        "2026-04-25",
        {"一次函数应用题": 0.52, "几何辅助线": 0.57, "基础运算": 0.90},
    ),
    (
        "SCORE-1002-01",
        "S1002",
        "EXAM-01",
        "春季入学测",
        "数学",
        76,
        100,
        82,
        25,
        35,
        "2026-03-05",
        {"一次函数应用题": 0.55, "基础运算": 0.78},
    ),
    (
        "SCORE-2001-01",
        "S2001",
        "EXAM-01",
        "春季入学测",
        "数学",
        93,
        100,
        84,
        4,
        38,
        "2026-03-05",
        {"一次函数应用题": 0.88, "几何辅助线": 0.86},
    ),
]

HOMEWORK = [
    ("HW-1001-01", "S1001", "数学", "2026-03-08", 1, 0.84, 1, ["一次函数"], "基础题完成较好"),
    (
        "HW-1001-02",
        "S1001",
        "数学",
        "2026-03-15",
        1,
        0.78,
        0,
        ["一次函数应用题"],
        "应用题过程书写不完整",
    ),
    ("HW-1001-03", "S1001", "数学", "2026-03-22", 1, 0.73, 0, ["几何辅助线"], "订正未完成"),
    (
        "HW-1001-04",
        "S1001",
        "数学",
        "2026-04-05",
        1,
        0.70,
        0,
        ["一次函数应用题"],
        "审题较快，遗漏条件",
    ),
    ("HW-1001-05", "S1001", "数学", "2026-04-19", 0, None, None, ["几何辅助线"], "未提交"),
    ("HW-1002-01", "S1002", "数学", "2026-03-08", 1, 0.72, 1, ["基础运算"], None),
]

ATTENDANCE = [
    ("AT-1001-01", "S1001", "COURSE-MATH-801", "2026-03-06", "present", 0, None),
    ("AT-1001-02", "S1001", "COURSE-MATH-801", "2026-03-13", "present", 0, None),
    ("AT-1001-03", "S1001", "COURSE-MATH-801", "2026-03-20", "late", 8, None),
    ("AT-1001-04", "S1001", "COURSE-MATH-801", "2026-04-03", "present", 0, None),
    ("AT-1001-05", "S1001", "COURSE-MATH-801", "2026-04-17", "present", 0, None),
    ("AT-1002-01", "S1002", "COURSE-MATH-801", "2026-03-06", "leave", 0, "病假"),
]

FEEDBACK = [
    (
        "FB-1001-01",
        "S1001",
        "T1001",
        "COURSE-MATH-801",
        "数学",
        "2026-03-12",
        ["课堂参与积极", "审题过快"],
        "回答问题积极，但应用题容易遗漏限制条件。",
    ),
    (
        "FB-1001-02",
        "S1001",
        "T1001",
        "COURSE-MATH-801",
        "数学",
        "2026-04-09",
        ["过程书写不完整", "订正不及时"],
        "理解思路较快，过程表达和错题订正需要加强。",
    ),
    (
        "FB-1002-01",
        "S1002",
        "T1001",
        "COURSE-MATH-801",
        "数学",
        "2026-03-12",
        ["基础需巩固"],
        "基础运算速度偏慢。",
    ),
]


def seed_database(database_path: Path, *, reset: bool = False) -> None:
    database_path.parent.mkdir(parents=True, exist_ok=True)
    if reset and database_path.exists():
        database_path.unlink()

    with sqlite3.connect(database_path) as connection:
        connection.executescript(SCHEMA)
        task_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(analysis_tasks)").fetchall()
        }
        if "execution_json" not in task_columns:
            connection.execute("ALTER TABLE analysis_tasks ADD COLUMN execution_json TEXT")
        connection.executemany(
            "INSERT OR REPLACE INTO students VALUES (?, ?, ?, ?, ?, ?, ?)", STUDENTS
        )
        connection.executemany(
            "INSERT OR REPLACE INTO teacher_student_access VALUES (?, ?)", ACCESS
        )
        connection.executemany(
            "INSERT OR REPLACE INTO scores VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [(*row[:-1], json.dumps(row[-1], ensure_ascii=False)) for row in SCORES],
        )
        connection.executemany(
            "INSERT OR REPLACE INTO homework_records VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [(*row[:7], json.dumps(row[7], ensure_ascii=False), row[8]) for row in HOMEWORK],
        )
        connection.executemany(
            "INSERT OR REPLACE INTO attendance_records VALUES (?, ?, ?, ?, ?, ?, ?)",
            ATTENDANCE,
        )
        connection.executemany(
            "INSERT OR REPLACE INTO classroom_feedback VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [(*row[:6], json.dumps(row[6], ensure_ascii=False), row[7]) for row in FEEDBACK],
        )


def main() -> None:
    settings = get_settings()
    seed_database(settings.database_path, reset=True)
    print(f"Seeded demo database: {settings.database_path}")


if __name__ == "__main__":
    main()
