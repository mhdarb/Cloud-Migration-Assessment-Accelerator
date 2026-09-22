from __future__ import annotations

import json

from app.schemas.api import ExtractedClaim, ExtractedDependency, ExtractionResult
from app.services.manifest_parsers.common import infra_from_dep, norm_app, quote


def parse_package_json(text: str, path: str) -> ExtractionResult:
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return ExtractionResult()
    name = data.get("name") or "node-app"
    app_key = norm_app(name)
    claims = [
        ExtractedClaim(
            entity_type="application",
            entity_key=app_key,
            attribute="name",
            value=str(name),
            confidence=0.92,
            evidence_quote=quote(path, f'"name": "{name}"'),
            chunk_ids=[],
        ),
        ExtractedClaim(
            entity_type="application",
            entity_key=app_key,
            attribute="runtime",
            value="node",
            confidence=0.9,
            evidence_quote=quote(path, "package.json indicates Node.js runtime"),
            chunk_ids=[],
        ),
    ]
    if data.get("engines", {}).get("node"):
        claims.append(
            ExtractedClaim(
                entity_type="application",
                entity_key=app_key,
                attribute="runtime_version",
                value=str(data["engines"]["node"]),
                confidence=0.85,
                evidence_quote=quote(path, f"engines.node={data['engines']['node']}"),
                chunk_ids=[],
            )
        )
    deps: list[ExtractedDependency] = []
    all_deps = {**data.get("dependencies", {}), **data.get("devDependencies", {})}
    framework = None
    if "express" in all_deps:
        framework = "express"
    elif "next" in all_deps:
        framework = "next.js"
    elif "@nestjs/core" in all_deps:
        framework = "nest.js"
    if framework:
        claims.append(
            ExtractedClaim(
                entity_type="application",
                entity_key=app_key,
                attribute="framework",
                value=framework,
                confidence=0.88,
                evidence_quote=quote(path, f"dependency {framework}"),
                chunk_ids=[],
            )
        )
    extra_claims: list[ExtractedClaim] = []
    for dep in all_deps:
        for item in infra_from_dep(dep, app_key, path):
            if isinstance(item, ExtractedClaim):
                extra_claims.append(item)
            else:
                deps.append(item)
    return ExtractionResult(claims=claims + extra_claims, dependencies=deps)

