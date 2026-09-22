from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

from app.schemas.api import ExtractedClaim, ExtractedDependency, ExtractionResult
from app.services.manifest_parsers import parse_manifest_file
from app.services.manifest_parsers.common import (
    MANIFEST_GLOBS,
    MANIFEST_NAMES,
    MAX_FILE_BYTES,
    SKIP_EXT,
)
from app.services.storage import safe_extract_zip

logger = logging.getLogger(__name__)


@dataclass
class ManifestFile:
    relative_path: str
    text: str


@dataclass
class ManifestScanResult:
    files: list[ManifestFile] = field(default_factory=list)
    extraction: ExtractionResult = field(default_factory=ExtractionResult)
    gaps: list[str] = field(default_factory=list)


def process_code_snapshot(zip_path: str, assessment_id: str, document_id: str) -> ManifestScanResult:
    root = safe_extract_zip(zip_path, assessment_id, document_id)
    manifests = _discover_manifests(root)
    if not manifests:
        return ManifestScanResult(
            gaps=["Code snapshot ZIP contained no recognizable manifests/config files"]
        )

    claims: list[ExtractedClaim] = []
    deps: list[ExtractedDependency] = []
    app_keys: set[str] = set()

    for mf in manifests:
        items = parse_manifest_file(mf.relative_path, mf.text)
        for c in items.claims:
            claims.append(c)
            if c.entity_type == "application" and c.attribute == "name":
                app_keys.add(c.entity_key)
        deps.extend(items.dependencies)

    if not app_keys:
        claims.append(
            ExtractedClaim(
                entity_type="application",
                entity_key="snapshot-app",
                attribute="name",
                value="Snapshot App",
                confidence=0.55,
                evidence_quote="Derived from code snapshot (no package/pom name found)",
                chunk_ids=[],
            )
        )

    return ManifestScanResult(
        files=manifests,
        extraction=ExtractionResult(claims=claims, dependencies=deps, gaps=[]),
        gaps=[],
    )


def _resolve_chunk_id(
    quote: str | None, path_to_chunk: dict[str, list[tuple[str, str]]]
) -> str:
    """Pick the chunk id for a manifest-derived claim's quote.

    A manifest file may now span multiple chunks (see `chunkers.chunk_code_manifest_file`),
    so disambiguate by checking which of the file's chunks actually contains the quote text
    (same substring approach as `citations.quote_grounded`), falling back to the file's
    first chunk when the quote can't be matched to a specific piece.
    """
    path = _path_from_quote(quote)
    candidates = path_to_chunk.get(path or "", [])
    if not candidates:
        for p, items in path_to_chunk.items():
            if p in (quote or ""):
                candidates = items
                break
    if not candidates:
        return ""
    if len(candidates) == 1 or not quote:
        return candidates[0][0]
    stripped_quote = re.sub(r"^\[[^\]]+\]\s*", "", quote).strip()
    needle = stripped_quote.lower()
    if needle:
        for cid, text in candidates:
            if needle in text.lower():
                return cid
    return candidates[0][0]


def attach_chunk_ids(
    result: ManifestScanResult, path_to_chunk: dict[str, list[tuple[str, str]]]
) -> ExtractionResult:
    """Rewrite evidence chunk_ids using persisted chunk map keyed by relative path."""
    claims: list[ExtractedClaim] = []
    for c in result.extraction.claims:
        cid = _resolve_chunk_id(c.evidence_quote, path_to_chunk)
        claims.append(
            ExtractedClaim(
                entity_type=c.entity_type,
                entity_key=c.entity_key,
                attribute=c.attribute,
                value=c.value,
                confidence=c.confidence,
                evidence_quote=c.evidence_quote,
                chunk_ids=[cid] if cid else [],
            )
        )
    deps: list[ExtractedDependency] = []
    for d in result.extraction.dependencies:
        cid = _resolve_chunk_id(d.evidence_quote, path_to_chunk)
        deps.append(
            ExtractedDependency(
                source_type=d.source_type,
                source_key=d.source_key,
                target_type=d.target_type,
                target_key=d.target_key,
                relationship=d.relationship,
                confidence=d.confidence,
                evidence_quote=d.evidence_quote,
                chunk_ids=[cid] if cid else [],
            )
        )
    return ExtractionResult(
        claims=claims,
        dependencies=deps,
        gaps=result.gaps + result.extraction.gaps,
        assumptions=result.extraction.assumptions,
    )


def _path_from_quote(quote: str | None) -> str | None:
    if not quote:
        return None
    m = re.match(r"^\[([^\]]+)\]", quote)
    return m.group(1) if m else None


def _discover_manifests(root: Path) -> list[ManifestFile]:
    found: list[ManifestFile] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix.lower() in SKIP_EXT:
            continue
        if path.stat().st_size > MAX_FILE_BYTES:
            continue
        rel = str(path.relative_to(root)).replace("\\", "/")
        name = path.name
        name_l = name.lower()
        include = name_l in MANIFEST_NAMES or name.lower() == "dockerfile"
        if not include:
            for pattern in MANIFEST_GLOBS:
                if path.match(pattern) or Path(name).match(pattern):
                    include = True
                    break
        if not include and name_l.endswith((".yml", ".yaml")):
            if any(
                k in rel.lower()
                for k in ("docker", "k8s", "helm", "deploy", "config", "values")
            ):
                include = True
        if not include:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        if not text.strip():
            continue
        found.append(ManifestFile(relative_path=rel, text=text[:MAX_FILE_BYTES]))
    return found
