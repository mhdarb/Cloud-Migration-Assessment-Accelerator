from __future__ import annotations

import logging
import posixpath
import re
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from app.schemas.api import ExtractedClaim, ExtractedDependency, ExtractionResult
from app.services.manifest_parsers import parse_manifest_file
from app.services.manifest_parsers.common import (
    MANIFEST_GLOBS,
    MANIFEST_NAMES,
    MAX_FILE_BYTES,
    SKIP_EXT,
    norm_app,
)
from app.services.manifest_parsers.docker import BUILD_CONTEXT_ATTRIBUTE
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

    parsed = [(mf, parse_manifest_file(mf.relative_path, mf.text)) for mf in manifests]
    claims, deps = _resolve_components(parsed)
    app_keys = {c.entity_key for c in claims if c.entity_type == "application" and c.attribute == "name"}

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


# --------------------------------------------------------------------------- #
# Component resolution: generic placeholder names -> the concrete service they belong to
# --------------------------------------------------------------------------- #
# Each parser sees one file in isolation, so a manifest that carries no name of its own
# falls back to a placeholder: every Dockerfile is "container-app", every requirements.txt
# "python-app". Across a multi-service snapshot that (a) lists placeholders beside the real
# services ("Container App", "Python App", "api" next to "zephyr-order-api") and (b) blends
# unrelated services into one fake entity — two Dockerfiles in two services both became
# "container-app", mixing a Node and a Python app's facts. Resolution happens here, where
# every file is visible: a file's facts belong to the component that owns its directory.
GENERIC_APP_KEYS = frozenset(
    {"container-app", "python-app", "dotnet-app", "java-app", "node-app", "k8s-app", "snapshot-app"}
)
# Directory names that say nothing about which service they hold; resolution looks past them.
_GENERIC_DIRS = frozenset(
    {"", ".", "src", "app", "apps", "docker", "deploy", "deployment", "build", "config", "configs",
     "k8s", "kubernetes", "helm", "infra", "ops", "ci", "scripts", "services"}
)
# Manifests that name their component, most authoritative first.
_NAMING_MANIFESTS = ("package.json", "pyproject.toml", ".csproj", "pom.xml", "build.gradle")


def _naming_rank(filename: str) -> int | None:
    name = filename.lower()
    for rank, suffix in enumerate(_NAMING_MANIFESTS):
        if name == suffix or name.endswith(suffix) or name.startswith(suffix):
            return rank
    return None


def _named_components(parsed: list[tuple[ManifestFile, ExtractionResult]]) -> dict[str, str]:
    """directory -> concrete component key, from the naming manifests in that directory."""
    best: dict[str, tuple[int, str]] = {}
    for mf, items in parsed:
        rank = _naming_rank(Path(mf.relative_path).name)
        if rank is None:
            continue
        for c in items.claims:
            if c.entity_type == "application" and c.attribute == "name" and c.entity_key not in GENERIC_APP_KEYS:
                directory = str(PurePosixPath(mf.relative_path).parent)
                if directory not in best or rank < best[directory][0]:
                    best[directory] = (rank, c.entity_key)
                break
    return {d: key for d, (_, key) in best.items()}


def _owner(directory: str, named: dict[str, str]) -> tuple[str, bool] | None:
    """(component key, is_named) owning `directory`: the named component there, else a
    meaningful directory name; generic directories ("src", "docker") defer to their parent."""
    path = PurePosixPath(directory)
    for d in (path, *path.parents):
        key = str(d)
        if key in named:
            return named[key], True
        if d.name.lower() not in _GENERIC_DIRS:
            return norm_app(d.name), False
    return None


def _resolve_components(
    parsed: list[tuple[ManifestFile, ExtractionResult]],
) -> tuple[list[ExtractedClaim], list[ExtractedDependency]]:
    named = _named_components(parsed)
    claims: list[ExtractedClaim] = []
    deps: list[ExtractedDependency] = []
    for mf, items in parsed:
        directory = str(PurePosixPath(mf.relative_path).parent)
        remap: dict[str, str] = {}
        from_named: set[str] = set()
        # 1) placeholder names -> the component owning this file's directory
        owner = _owner(directory, named)
        for c in items.claims:
            if c.entity_type == "application" and c.entity_key in GENERIC_APP_KEYS and owner:
                remap[c.entity_key] = owner[0]
                if owner[1]:
                    from_named.add(c.entity_key)
        # 2) compose services built from source -> the component in their build directory
        for c in items.claims:
            if c.attribute == BUILD_CONTEXT_ATTRIBUTE:
                built = _owner(posixpath.normpath(posixpath.join(directory, c.value)), named)
                if built and built[0] != c.entity_key:
                    remap[c.entity_key] = built[0]
                    if built[1]:
                        from_named.add(c.entity_key)

        for c in items.claims:
            if c.attribute == BUILD_CONTEXT_ATTRIBUTE:
                continue  # internal signal, not a fact about the estate
            target = remap.get(c.entity_key) if c.entity_type == "application" else None
            if target is None:
                claims.append(c)
            elif c.attribute == "name":
                # The concrete component carries its own name claim; a placeholder label
                # ("Container App") would only contradict it. Without a named manifest the
                # directory name is the best available name.
                if c.entity_key not in from_named:
                    claims.append(c.model_copy(update={"entity_key": target, "value": target}))
            else:
                claims.append(c.model_copy(update={"entity_key": target}))
        for d in items.dependencies:
            deps.append(
                d.model_copy(
                    update={
                        "source_key": remap.get(d.source_key, d.source_key)
                        if d.source_type == "application"
                        else d.source_key,
                        "target_key": remap.get(d.target_key, d.target_key)
                        if d.target_type == "application"
                        else d.target_key,
                    }
                )
            )
    return claims, deps


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
