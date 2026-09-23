from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models.entities import Chunk


def embeddings_dir() -> Path:
    return get_settings().embeddings_path


def faiss_index_path(assessment_id: str) -> Path:
    return embeddings_dir() / f"{assessment_id}.faiss"


def faiss_meta_path(assessment_id: str) -> Path:
    return embeddings_dir() / f"{assessment_id}.meta.json"


def get_chunks_by_ids(db: Session, chunk_ids: list[str]) -> list[Chunk]:
    if not chunk_ids:
        return []
    rows = db.query(Chunk).filter(Chunk.id.in_(chunk_ids)).all()
    by_id = {c.id: c for c in rows}
    return [by_id[cid] for cid in chunk_ids if cid in by_id]


class FaissVectorIndex:
    """Reads the on-disk index once per assessment and keeps it in memory for the rest of
    this instance's lifetime -- a single extraction run can issue dozens of `search()`
    calls (bounded by `AGENT_MAX_LLM_CALLS`) against data that never changes mid-run, so
    re-reading and deserializing the index file from disk on every call is pure waste.
    `BM25Retriever` already caches its corpus the same way; this closes the gap."""

    name = "faiss"

    def __init__(self) -> None:
        # assessment_id -> (index file mtime when loaded, faiss.Index, chunk_ids)
        self._cache: dict[str, tuple[float, Any, list[str]]] = {}

    def upsert(
        self, assessment_id: str, chunks: list[Chunk], vectors: list[list[float]]
    ) -> int:
        import faiss

        embeddings_dir().mkdir(parents=True, exist_ok=True)
        matrix = np.asarray(vectors, dtype=np.float32)
        if matrix.ndim != 2 or matrix.size == 0:
            return 0
        faiss.normalize_L2(matrix)
        index = faiss.IndexFlatIP(matrix.shape[1])
        index.add(matrix)
        faiss.write_index(index, str(faiss_index_path(assessment_id)))
        faiss_meta_path(assessment_id).write_text(
            json.dumps(
                {
                    "assessment_id": assessment_id,
                    "dim": int(matrix.shape[1]),
                    "chunk_ids": [c.id for c in chunks],
                }
            ),
            encoding="utf-8",
        )
        # upsert always fully replaces the index (see above) -- drop any cached copy so
        # the next search() reloads it rather than serving stale results from before
        # this write, on the off chance this instance's cache is still warm from an
        # earlier round in the same run (e.g. a follow-up ingest of added documents).
        self._cache.pop(assessment_id, None)
        return len(chunks)

    def search(
        self,
        db: Session,
        assessment_id: str,
        query: str,
        query_vector: list[float],
        top_k: int,
    ) -> list[Chunk]:
        import faiss

        meta_path = faiss_meta_path(assessment_id)
        index_path = faiss_index_path(assessment_id)
        if not meta_path.exists() or not index_path.exists():
            self._cache.pop(assessment_id, None)
            return []

        mtime = index_path.stat().st_mtime
        cached = self._cache.get(assessment_id)
        if cached is not None and cached[0] == mtime:
            index, chunk_ids = cached[1], cached[2]
        else:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            chunk_ids = meta.get("chunk_ids") or []
            if not chunk_ids:
                self._cache.pop(assessment_id, None)
                return []
            index = faiss.read_index(str(index_path))
            self._cache[assessment_id] = (mtime, index, chunk_ids)

        qvec = np.asarray([query_vector], dtype=np.float32)
        faiss.normalize_L2(qvec)
        k = min(top_k, len(chunk_ids))
        scores, idxs = index.search(qvec, k)
        threshold = get_settings().vector_score_threshold
        ids: list[str] = []
        for score, raw_idx in zip(scores[0], idxs[0], strict=True):
            if raw_idx < 0 or score <= threshold:
                continue
            ids.append(chunk_ids[int(raw_idx)])
        return get_chunks_by_ids(db, ids)

    def clear(self, assessment_id: str) -> None:
        self._cache.pop(assessment_id, None)
        for path in (faiss_index_path(assessment_id), faiss_meta_path(assessment_id)):
            if path.exists():
                path.unlink()


class AzureSearchIndex:
    name = "azure_search"

    def upsert(
        self, assessment_id: str, chunks: list[Chunk], vectors: list[list[float]]
    ) -> int:
        from app.azure_clients import get_search_client

        client = get_search_client()
        docs = []
        for c, vec in zip(chunks, vectors, strict=True):
            docs.append(
                {
                    "id": c.id,
                    "assessment_id": c.assessment_id,
                    "document_id": c.document_id,
                    "chunk_index": c.chunk_index,
                    "page": c.page or 0,
                    "content": c.text,
                    "content_vector": vec,
                }
            )
        client.upload_documents(documents=docs)
        return len(docs)

    def search(
        self,
        db: Session,
        assessment_id: str,
        query: str,
        query_vector: list[float],
        top_k: int,
    ) -> list[Chunk]:
        from azure.search.documents.models import VectorizedQuery

        from app.azure_clients import get_search_client

        client = get_search_client()
        vector_query = VectorizedQuery(
            vector=query_vector,
            k_nearest_neighbors=top_k,
            fields="content_vector",
        )
        results = client.search(
            search_text=query,
            vector_queries=[vector_query],
            filter=f"assessment_id eq '{assessment_id}'",
            top=top_k,
        )
        ids = [r["id"] for r in results]
        return get_chunks_by_ids(db, ids)

    def clear(self, assessment_id: str) -> None:
        return None
