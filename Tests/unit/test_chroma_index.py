from pathlib import Path

from futureedu_insight.rag.case_store import JsonCaseStore
from futureedu_insight.rag.chroma_index import ChromaCaseIndex

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_chroma_index_persists_and_filters_case_vectors(tmp_path) -> None:
    index = ChromaCaseIndex(tmp_path / "chroma")
    index.sync(JsonCaseStore(PROJECT_ROOT / "data" / "cases").load())

    results = index.search(
        "八年级数学 一次函数应用题 审题过快",
        grade="八年级",
        subject="数学",
        limit=3,
    )

    assert results
    assert next(iter(results)) == "CASE-MATH-0001"
    assert all(case_id.startswith("CASE-MATH") for case_id in results)
