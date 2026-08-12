from __future__ import annotations

import argparse
import json
import math
import time
from datetime import date
from pathlib import Path
from statistics import mean
from typing import Any

from futureedu_insight.config import PROJECT_ROOT
from futureedu_insight.domain.models import DateRange, LearningProfile
from futureedu_insight.rag.case_store import JsonCaseStore
from futureedu_insight.rag.hybrid_retriever import HybridCaseRetriever


def _profile(item: dict[str, Any]) -> LearningProfile:
    return LearningProfile(
        student_id="EVAL-STUDENT",
        grade=item["grade"],
        subject=item["subject"],
        period=DateRange(start=date(2026, 3, 1), end=date(2026, 4, 30)),
        rules_version="eval-1.0",
        score_trend=item["score_trend"],
        weak_knowledge_points=item["weak_knowledge_points"],
        classroom_tags=item["classroom_tags"],
        learning_tags=item["learning_tags"],
        data_completeness=1.0,
    )


def evaluate_rag(dataset_path: Path, cases_path: Path, *, top_k: int = 3) -> dict[str, Any]:
    dataset = json.loads(dataset_path.read_text(encoding="utf-8"))
    retriever = HybridCaseRetriever(JsonCaseStore(cases_path), min_relevance=0.2, final_count=top_k)
    results: list[dict[str, Any]] = []
    latencies: list[float] = []
    reciprocal_ranks: list[float] = []
    hits = 0
    for item in dataset:
        started = time.perf_counter()
        retrieved = retriever.retrieve(_profile(item))
        latencies.append((time.perf_counter() - started) * 1000)
        actual = [result.case.case_id for result in retrieved]
        expected = set(item["expected_case_ids"])
        first_rank = next(
            (index for index, case_id in enumerate(actual, start=1) if case_id in expected),
            None,
        )
        if first_rank is not None:
            hits += 1
            reciprocal_ranks.append(1 / first_rank)
        else:
            reciprocal_ranks.append(0)
        results.append(
            {
                "name": item["name"],
                "expected": sorted(expected),
                "retrieved": actual,
                "first_relevant_rank": first_rank,
            }
        )

    sorted_latency = sorted(latencies)
    p95_index = max(0, min(len(sorted_latency) - 1, math.ceil(len(sorted_latency) * 0.95) - 1))
    return {
        "dataset_size": len(dataset),
        f"recall_at_{top_k}": round(hits / len(dataset), 4),
        "mrr": round(mean(reciprocal_ranks), 4),
        "average_latency_ms": round(mean(latencies), 3),
        "p95_latency_ms": round(sorted_latency[p95_index], 3),
        "results": results,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run fixed-version offline evaluations")
    parser.add_argument(
        "--dataset",
        type=Path,
        default=PROJECT_ROOT / "evals" / "datasets" / "rag_cases.json",
    )
    parser.add_argument("--cases", type=Path, default=PROJECT_ROOT / "data" / "cases")
    parser.add_argument("--top-k", type=int, default=3)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    report = evaluate_rag(args.dataset, args.cases, top_k=args.top_k)
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)


if __name__ == "__main__":
    main()
