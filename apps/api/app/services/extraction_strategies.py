"""Extraction strategies beyond the default LLM-with-heuristic-fallback (Strategy pattern:
`ChatLlmExtractor` / `HeuristicLlmExtractor` / `EnsembleExtractor` are interchangeable
`LlmExtractor` implementations selected by `settings.extraction_strategy`)."""

from __future__ import annotations

from pydantic import BaseModel

from app.schemas.api import ExtractionResult
from app.schemas.chunking import ChunkPayload
from app.services.extraction_merge import dedupe_extraction
from app.services.ports import LlmExtractor


class EnsembleExtractor:
    """Runs both the heuristic (good at structured/tabular chunks) and LLM (good at
    prose) extractors and merges via the existing dedupe — higher recall, higher cost.
    Opt-in via `EXTRACTION_STRATEGY=ensemble`; not the default."""

    def __init__(self, heuristic: LlmExtractor, llm: LlmExtractor) -> None:
        self._heuristic = heuristic
        self._llm = llm

    def extract(
        self,
        query: str,
        chunk_payload: list[ChunkPayload],
        *,
        target_schema: type[BaseModel] | None = None,
        system_prompt_suffix: str | None = None,
    ) -> ExtractionResult:
        heuristic_result = self._heuristic.extract(query, chunk_payload)
        try:
            llm_result = self._llm.extract(
                query,
                chunk_payload,
                target_schema=target_schema,
                system_prompt_suffix=system_prompt_suffix,
            )
        except Exception:
            llm_result = ExtractionResult()
        return dedupe_extraction(
            ExtractionResult(
                claims=[*heuristic_result.claims, *llm_result.claims],
                dependencies=[*heuristic_result.dependencies, *llm_result.dependencies],
                gaps=[*heuristic_result.gaps, *llm_result.gaps],
                assumptions=[*heuristic_result.assumptions, *llm_result.assumptions],
            )
        )
