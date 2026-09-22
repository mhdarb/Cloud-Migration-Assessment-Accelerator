from __future__ import annotations

import re
from collections.abc import Mapping

from app.schemas.api import ExtractionResult


def normalize_for_quote_match(text: str) -> str:
    """Collapse whitespace for resilient substring checks."""
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def _quote_needles(quote: str) -> list[str]:
    """Build match needles; strip manifest `[path]` prefixes used by code parsers."""
    raw = quote.strip()
    stripped = re.sub(r"^\[[^\]]+\]\s*", "", raw).strip()
    needles = []
    for part in (raw, stripped):
        n = normalize_for_quote_match(part)
        if not n:
            continue
        needles.append(n)
        if len(n) > 40:
            needles.append(n[:40])
            needles.append(n[-40:])
    # dedupe preserve order
    seen: set[str] = set()
    out: list[str] = []
    for n in needles:
        if n not in seen:
            seen.add(n)
            out.append(n)
    return out


def quote_grounded(quote: str | None, chunk_texts: list[str]) -> bool:
    """True when the evidence quote (or a substantial fragment) appears in chunk text."""
    if not quote or not quote.strip():
        return False
    candidates = _quote_needles(quote)
    if not candidates:
        return False
    for text in chunk_texts:
        hay = normalize_for_quote_match(text)
        if any(c in hay for c in candidates if len(c) >= 6):
            return True
    return False


def validate_citations(
    result: ExtractionResult,
    allowed_chunk_ids: set[str],
    chunk_texts: Mapping[str, str] | None = None,
) -> ExtractionResult:
    """
    Enforce evidence integrity:
    - chunk_ids must be in the retrieved/allowed set
    - when chunk texts are provided, evidence_quote must be grounded in those chunks
    Unsupported / ungrounded claims are cleared of refs and confidence-capped.
    """
    texts = chunk_texts or {}

    def _check(chunk_ids: list[str], quote: str | None) -> tuple[list[str], float | None]:
        valid = [cid for cid in chunk_ids if cid in allowed_chunk_ids]
        if not valid:
            return [], 0.35
        if texts:
            grounded_ids = [
                cid for cid in valid if quote_grounded(quote, [texts.get(cid, "")])
            ]
            if not grounded_ids:
                # try grounding against any of the cited chunks' combined text
                combined = [texts.get(cid, "") for cid in valid]
                if quote_grounded(quote, combined):
                    return valid, None
                return [], 0.35
            return grounded_ids, None
        return valid, None

    for claim in result.claims:
        valid, cap = _check(claim.chunk_ids, claim.evidence_quote)
        claim.chunk_ids = valid
        if cap is not None:
            claim.confidence = min(claim.confidence, cap)

    for dep in result.dependencies:
        valid, cap = _check(dep.chunk_ids, dep.evidence_quote)
        dep.chunk_ids = valid
        if cap is not None:
            dep.confidence = min(dep.confidence, cap)

    return result
