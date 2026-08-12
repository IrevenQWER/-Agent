from __future__ import annotations

import hashlib
import math
import re
from collections import Counter
from typing import Protocol

import numpy as np
from rank_bm25 import BM25Okapi

from futureedu_insight.domain.models import (
    HistoricalCase,
    LearningProfile,
    RetrievedCase,
    ScoreTrend,
)
from futureedu_insight.rag.case_store import JsonCaseStore

TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_.-]+|[\u4e00-\u9fff]+")


class VectorCaseIndex(Protocol):
    def search(
        self, query: str, *, grade: str, subject: str, limit: int = 20
    ) -> dict[str, float]: ...


def tokenize(text: str) -> list[str]:
    tokens: list[str] = []
    for match in TOKEN_PATTERN.findall(text.lower()):
        if re.fullmatch(r"[\u4e00-\u9fff]+", match):
            tokens.extend(match)
            tokens.extend(match[index : index + 2] for index in range(len(match) - 1))
        else:
            tokens.append(match)
    return tokens


def hashed_embedding(text: str, dimensions: int = 384) -> np.ndarray:
    vector = np.zeros(dimensions, dtype=float)
    for token, count in Counter(tokenize(text)).items():
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        index = int.from_bytes(digest[:4], "big") % dimensions
        vector[index] += 1 + math.log(count)
    norm = np.linalg.norm(vector)
    return vector / norm if norm else vector


def _cosine(left: np.ndarray, right: np.ndarray) -> float:
    if not left.any() or not right.any():
        return 0.0
    return float(np.clip(np.dot(left, right), 0, 1))


def build_case_query(profile: LearningProfile) -> str:
    """Build a versionable query from deterministic profile fields."""

    values = [
        profile.grade,
        profile.subject,
        f"成绩趋势 {profile.score_trend.value}",
        " ".join(profile.learning_tags),
        " ".join(profile.weak_knowledge_points),
        " ".join(profile.classroom_tags),
    ]
    if profile.homework_submission_rate is not None:
        values.append(f"作业提交率 {profile.homework_submission_rate:.2f}")
    if profile.correction_rate is not None:
        values.append(f"错题订正率 {profile.correction_rate:.2f}")
    return "。".join(value for value in values if value)


def _case_text(case: HistoricalCase) -> str:
    return "。".join(
        (
            case.grade,
            case.subject,
            case.score_trend.value,
            " ".join(case.problem_types),
            case.profile_text,
            " ".join(case.applicable_conditions),
        )
    )


class HybridCaseRetriever:
    """Metadata-filtered vector + BM25 retrieval with deterministic reranking."""

    def __init__(
        self,
        store: JsonCaseStore,
        *,
        min_relevance: float = 0.35,
        final_count: int = 3,
        vector_index: VectorCaseIndex | None = None,
    ) -> None:
        self.store = store
        self.min_relevance = min_relevance
        self.final_count = final_count
        self.vector_index = vector_index

    @staticmethod
    def _tag_overlap(profile: LearningProfile, case: HistoricalCase) -> float:
        profile_terms = set(profile.weak_knowledge_points)
        profile_terms.update(profile.classroom_tags)
        profile_terms.update(profile.learning_tags)
        case_terms = set(case.problem_types)
        case_tokens = set(tokenize(case.profile_text))
        direct = len(profile_terms & case_terms)
        fuzzy = sum(
            any(term in token or token in term for token in case_tokens) for term in profile_terms
        )
        denominator = max(len(profile_terms), 1)
        trend_bonus = 1 if profile.score_trend == case.score_trend else 0
        return min((direct + 0.5 * fuzzy + trend_bonus) / (denominator + 1), 1.0)

    def retrieve(self, profile: LearningProfile) -> list[RetrievedCase]:
        candidates = [
            case
            for case in self.store.load()
            if case.grade == profile.grade
            and case.subject == profile.subject
            and (
                profile.score_trend == ScoreTrend.INSUFFICIENT
                or case.score_trend == profile.score_trend
            )
        ]
        if not candidates:
            return []

        query = build_case_query(profile)
        query_tokens = tokenize(query)
        case_texts = [_case_text(case) for case in candidates]
        corpus_tokens = [tokenize(text) for text in case_texts]

        bm25 = BM25Okapi(corpus_tokens)
        raw_keyword_scores = bm25.get_scores(query_tokens)
        max_keyword = float(max(raw_keyword_scores)) if len(raw_keyword_scores) else 0.0
        keyword_scores = [
            float(score / max_keyword) if max_keyword > 0 else 0.0 for score in raw_keyword_scores
        ]

        query_vector = hashed_embedding(query)
        indexed_scores = (
            self.vector_index.search(
                query,
                grade=profile.grade,
                subject=profile.subject,
                limit=max(self.final_count * 5, 20),
            )
            if self.vector_index
            else {}
        )
        results: list[RetrievedCase] = []
        for case, text, keyword_score in zip(candidates, case_texts, keyword_scores, strict=True):
            vector_score = indexed_scores.get(
                case.case_id, _cosine(query_vector, hashed_embedding(text))
            )
            overlap_score = self._tag_overlap(profile, case)
            relevance = 0.45 * vector_score + 0.25 * keyword_score + 0.30 * overlap_score
            if relevance < self.min_relevance:
                continue
            reasons: list[str] = []
            if case.score_trend == profile.score_trend:
                reasons.append("成绩趋势一致")
            for point in profile.weak_knowledge_points:
                if point in case.profile_text or any(point in item for item in case.problem_types):
                    reasons.append(f"共同薄弱点：{point}")
            for tag in profile.classroom_tags:
                if tag in case.profile_text:
                    reasons.append(f"共同课堂表现：{tag}")
            if not reasons:
                reasons.append("学习画像语义相似")

            citation = (
                f"{case.case_id}：{case.intervention}；随后在{case.observation_period}内"
                f"观察到“{case.observed_metric}”由{case.before_value}变化为{case.after_value}。"
            )
            results.append(
                RetrievedCase(
                    case=case,
                    vector_score=round(vector_score, 4),
                    keyword_score=round(keyword_score, 4),
                    relevance_score=round(relevance, 4),
                    similarity_reasons=list(dict.fromkeys(reasons)),
                    citation_text=citation,
                )
            )

        results.sort(key=lambda result: (-result.relevance_score, result.case.case_id))
        return results[: self.final_count]
