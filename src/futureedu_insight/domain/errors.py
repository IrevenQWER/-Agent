class LearningInsightError(Exception):
    """Base application error."""


class InvalidRequestError(LearningInsightError):
    """The user request is unsupported or incomplete."""


class AmbiguousStudentError(LearningInsightError):
    def __init__(self, candidates: list[dict[str, str]]) -> None:
        super().__init__("学生标识存在歧义，需要补充信息")
        self.candidates = candidates


class StudentNotFoundError(LearningInsightError):
    """No student in the teacher's authorized roster matched the identifier."""


class PermissionDeniedError(LearningInsightError):
    """The authenticated teacher cannot access the requested student."""


class InsufficientDataError(LearningInsightError):
    def __init__(self, missing_sources: list[str]) -> None:
        super().__init__(f"学情数据不足: {', '.join(missing_sources)}")
        self.missing_sources = missing_sources


class ModelServiceError(LearningInsightError):
    """The configured language model could not complete the request."""


class ReportValidationError(LearningInsightError):
    """A generated or teacher-edited report failed fact validation."""
