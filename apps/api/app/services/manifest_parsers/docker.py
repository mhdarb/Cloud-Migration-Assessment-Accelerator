from __future__ import annotations

import re

from app.schemas.api import ExtractedClaim, ExtractedDependency, ExtractionResult
from app.services.manifest_parsers.common import INFRA_DEPS, infra_from_dep, norm_app, quote


def parse_dockerfile(text: str, path: str) -> ExtractionResult:
    claims: list[ExtractedClaim] = []
    deps: list[ExtractedDependency] = []
    app_key = "container-app"
    from_m = re.search(r"^FROM\s+(\S+)", text, re.I | re.M)
    if from_m:
        base = from_m.group(1)
        runtime = "unknown"
        if "node" in base.lower():
            runtime = "node"
        elif "python" in base.lower():
            runtime = "python"
        elif "eclipse-temurin" in base.lower() or "openjdk" in base.lower() or "jdk" in base.lower():
            runtime = "java"
        elif "dotnet" in base.lower():
            runtime = "dotnet"
        claims.append(
            ExtractedClaim(
                entity_type="application",
                entity_key=app_key,
                attribute="name",
                value="Container App",
                confidence=0.6,
                evidence_quote=quote(path, f"FROM {base}"),
                chunk_ids=[],
            )
        )
        claims.append(
            ExtractedClaim(
                entity_type="application",
                entity_key=app_key,
                attribute="runtime",
                value=runtime,
                confidence=0.8,
                evidence_quote=quote(path, f"FROM {base}"),
                chunk_ids=[],
            )
        )
        claims.append(
            ExtractedClaim(
                entity_type="application",
                entity_key=app_key,
                attribute="base_image",
                value=base,
                confidence=0.9,
                evidence_quote=quote(path, f"FROM {base}"),
                chunk_ids=[],
            )
        )
    for m in re.finditer(r"^EXPOSE\s+(\d+)", text, re.I | re.M):
        claims.append(
            ExtractedClaim(
                entity_type="application",
                entity_key=app_key,
                attribute="port",
                value=m.group(1),
                confidence=0.85,
                evidence_quote=quote(path, m.group(0)),
                chunk_ids=[],
            )
        )
    for key in ("postgres", "redis", "mongo", "kafka"):
        if key in text.lower():
            for item in infra_from_dep(key, app_key, path):
                if isinstance(item, ExtractedClaim):
                    claims.append(item)
                else:
                    deps.append(item)
    return ExtractionResult(claims=claims, dependencies=deps)

BUILD_CONTEXT_ATTRIBUTE = "build_context"  # consumed by code_manifests, never persisted


def _service_build_context(block: str) -> str | None:
    """`build: ./dir`, or `build:` followed by an indented `context: ./dir`."""
    # `[ \t]` rather than `\s`: the value must be on the same line as `build:` — `\s` would
    # run across the newline and read the next line's `context:` key as the build path.
    m = re.search(r"^[ \t]+build:[ \t]*['\"]?([^\s'\"#]+)", block, re.M)
    if m:
        return m.group(1)
    if re.search(r"^[ \t]+build:[ \t]*$", block, re.M):
        ctx = re.search(r"^[ \t]+context:[ \t]*['\"]?([^\s'\"#]+)", block, re.M)
        return ctx.group(1) if ctx else "."
    return None


def parse_compose(text: str, path: str) -> ExtractionResult:
    claims: list[ExtractedClaim] = []
    deps: list[ExtractedDependency] = []
    # naive service blocks: a two-space-indented `name:` header up to the next header
    headers = list(re.finditer(r"^\s{2}([a-zA-Z0-9_-]+):\s*$", text, re.M))
    for i, m in enumerate(headers):
        svc = m.group(1)
        if svc in {"services", "volumes", "networks", "version"}:
            continue
        block = text[m.end() : headers[i + 1].start() if i + 1 < len(headers) else len(text)]
        etype, ekey = "application", norm_app(svc)
        if any(k in svc.lower() for k in ("postgres", "db", "mysql", "mongo")):
            etype, ekey = "database", norm_app(svc)
        elif "redis" in svc.lower():
            etype, ekey = "database", "redis"
        elif "kafka" in svc.lower():
            etype, ekey = "interface", "kafka"
        claims.append(
            ExtractedClaim(
                entity_type=etype,
                entity_key=ekey,
                attribute="name",
                value=svc,
                confidence=0.8,
                evidence_quote=quote(path, f"service: {svc}"),
                chunk_ids=[],
            )
        )
        build_context = _service_build_context(block) if etype == "application" else None
        if build_context:
            # A service built from source *is* the component in that build directory
            # (e.g. `api` with `build: .` next to package.json "zephyr-order-api");
            # code_manifests uses this to fold the service name into the concrete one.
            claims.append(
                ExtractedClaim(
                    entity_type=etype,
                    entity_key=ekey,
                    attribute=BUILD_CONTEXT_ATTRIBUTE,
                    value=build_context,
                    confidence=0.8,
                    evidence_quote=quote(path, f"service: {svc} build: {build_context}"),
                    chunk_ids=[],
                )
            )
    image_hits = re.findall(r"image:\s*['\"]?([^\s'\"]+)", text, re.I)
    for img in image_hits:
        for key in INFRA_DEPS:
            if key in img.lower():
                claims.append(
                    ExtractedClaim(
                        entity_type=INFRA_DEPS[key][0],
                        entity_key=INFRA_DEPS[key][1],
                        attribute="name",
                        value=INFRA_DEPS[key][2],
                        confidence=0.85,
                        evidence_quote=quote(path, f"image: {img}"),
                        chunk_ids=[],
                    )
                )
    return ExtractionResult(claims=claims, dependencies=deps)

