from __future__ import annotations

import re

from app.schemas.api import ExtractedClaim, ExtractedDependency, ExtractionResult
from app.services.manifest_parsers.common import INFRA_DEPS, infra_from_dep, quote


def parse_env_example(text: str, path: str) -> ExtractionResult:
    claims: list[ExtractedClaim] = []
    app_key = "snapshot-app"
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key = line.split("=", 1)[0].strip()
        claims.append(
            ExtractedClaim(
                entity_type="application",
                entity_key=app_key,
                attribute="config_key",
                value=key,
                confidence=0.75,
                evidence_quote=quote(path, f"{key}=***"),
                chunk_ids=[],
            )
        )
    return ExtractionResult(claims=claims)

def parse_yaml_lite(text: str, path: str) -> ExtractionResult:
    claims: list[ExtractedClaim] = []
    deps: list[ExtractedDependency] = []
    app_key = "k8s-app"
    for m in re.finditer(r"containerPort:\s*(\d+)", text):
        claims.append(
            ExtractedClaim(
                entity_type="application",
                entity_key=app_key,
                attribute="port",
                value=m.group(1),
                confidence=0.8,
                evidence_quote=quote(path, m.group(0)),
                chunk_ids=[],
            )
        )
    for m in re.finditer(r"image:\s*['\"]?([^\s'\"]+)", text):
        claims.append(
            ExtractedClaim(
                entity_type="application",
                entity_key=app_key,
                attribute="image",
                value=m.group(1),
                confidence=0.8,
                evidence_quote=quote(path, m.group(0)),
                chunk_ids=[],
            )
        )
    for key in INFRA_DEPS:
        if key in text.lower():
            for item in infra_from_dep(key, app_key, path):
                if isinstance(item, ExtractedClaim):
                    claims.append(item)
                else:
                    deps.append(item)
    return ExtractionResult(claims=claims, dependencies=deps)

