from __future__ import annotations

import json
from pathlib import Path

from pydantic import TypeAdapter

from futureedu_insight.domain.models import HistoricalCase


class JsonCaseStore:
    """Read-only store for approved and de-identified historical cases."""

    def __init__(self, cases_path: Path | str) -> None:
        self.cases_path = Path(cases_path)
        self._adapter = TypeAdapter(list[HistoricalCase])

    def load(self) -> list[HistoricalCase]:
        documents: list[HistoricalCase] = []
        for path in sorted(self.cases_path.glob("*.json")):
            raw = json.loads(path.read_text(encoding="utf-8"))
            documents.extend(self._adapter.validate_python(raw))
        return [case for case in documents if case.approval_status == "approved"]
