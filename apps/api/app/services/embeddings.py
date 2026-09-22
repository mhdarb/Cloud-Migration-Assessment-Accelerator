from __future__ import annotations

import logging

import numpy as np

from app.config import get_settings

logger = logging.getLogger(__name__)

LOCAL_DIM = 384
_st_model = None


def describe_embedder() -> str:
    if get_settings().use_azure_embeddings:
        return "azure"
    return "sentence-transformers"


class SentenceTransformerEmbedder:
    """Local MiniLM embeddings. Fails if the model cannot load."""

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        model = sentence_model()
        vectors = model.encode(
            texts,
            convert_to_numpy=True,
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        return [v.astype(np.float32).tolist() for v in vectors]

    def embed_query(self, query: str) -> list[float]:
        return self.embed_texts([query])[0]


class AzureEmbedder:
    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        settings = get_settings()
        from app.azure_clients import get_azure_openai_client

        client = get_azure_openai_client()
        response = client.embeddings.create(
            model=settings.azure_openai_embedding_deployment,
            input=texts,
        )
        ordered = sorted(response.data, key=lambda d: d.index)
        return [list(d.embedding) for d in ordered]

    def embed_query(self, query: str) -> list[float]:
        return self.embed_texts([query])[0]


class FallbackEmbedder:
    def __init__(self, primary, fallback, *, reraise: bool = False) -> None:
        self._primary = primary
        self._fallback = fallback
        self._reraise = reraise

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        try:
            return self._primary.embed_texts(texts)
        except Exception:
            if self._reraise:
                raise
            logger.exception("Primary embedder failed; using fallback")
            return self._fallback.embed_texts(texts)

    def embed_query(self, query: str) -> list[float]:
        return self.embed_texts([query])[0]


def sentence_model():
    global _st_model
    if _st_model is not None:
        return _st_model
    settings = get_settings()
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError as exc:
        raise RuntimeError(
            "sentence-transformers is required for local FAISS embeddings"
        ) from exc
    logger.info("Loading local embedding model %s", settings.local_embedding_model)
    _st_model = SentenceTransformer(settings.local_embedding_model)
    return _st_model


def embedding_dim() -> int:
    settings = get_settings()
    if settings.use_azure_embeddings:
        return 1536
    model = sentence_model()
    try:
        return int(model.get_sentence_embedding_dimension())
    except Exception:
        return LOCAL_DIM
