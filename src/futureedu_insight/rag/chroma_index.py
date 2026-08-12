from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from futureedu_insight.domain.models import HistoricalCase

if TYPE_CHECKING:
    from chromadb.api.models.Collection import Collection


class ChromaCaseIndex:
    """Persistent Chroma vector index using caller-supplied deterministic embeddings."""

    def __init__(self, persist_path: Path | str, *, collection_name: str = "learning_cases"):
        try:
            import chromadb
        except ImportError as exc:
            raise RuntimeError("Chroma 未安装，请安装项目的 rag 可选依赖") from exc

        client = chromadb.PersistentClient(path=str(persist_path))
        self.collection: Collection = client.get_or_create_collection(
            collection_name,
            metadata={"hnsw:space": "cosine"},
        )

    def sync(self, cases: list[HistoricalCase]) -> None:
        from futureedu_insight.rag.hybrid_retriever import _case_text, hashed_embedding

        if not cases:
            return
        self.collection.upsert(
            ids=[case.case_id for case in cases],
            documents=[_case_text(case) for case in cases],
            embeddings=[hashed_embedding(_case_text(case)).tolist() for case in cases],
            metadatas=[
                {
                    "grade": case.grade,
                    "subject": case.subject,
                    "version": case.version,
                    "approval_status": case.approval_status,
                }
                for case in cases
            ],
        )

    def search(
        self,
        query: str,
        *,
        grade: str,
        subject: str,
        limit: int = 20,
    ) -> dict[str, float]:
        from futureedu_insight.rag.hybrid_retriever import hashed_embedding

        count = self.collection.count()
        if count == 0:
            return {}
        result = self.collection.query(
            query_embeddings=[hashed_embedding(query).tolist()],
            n_results=min(limit, count),
            where={"$and": [{"grade": grade}, {"subject": subject}]},
            include=["distances"],
        )
        ids = result.get("ids", [[]])[0]
        distances = result.get("distances", [[]])[0]
        return {
            case_id: max(0.0, min(1.0, 1.0 - float(distance)))
            for case_id, distance in zip(ids, distances, strict=True)
        }
