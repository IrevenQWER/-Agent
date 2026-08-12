from pathlib import Path

from futureedu_insight.evaluation.runner import evaluate_rag

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_fixed_rag_evaluation_meets_quality_gate() -> None:
    result = evaluate_rag(
        PROJECT_ROOT / "evals" / "datasets" / "rag_cases.json",
        PROJECT_ROOT / "data" / "cases",
        top_k=3,
    )

    assert result["dataset_size"] == 5
    assert result["recall_at_3"] >= 0.8
    assert result["mrr"] >= 0.7
