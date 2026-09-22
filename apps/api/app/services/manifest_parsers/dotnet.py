from __future__ import annotations

import json
import re
from pathlib import Path

from app.schemas.api import ExtractedClaim, ExtractedDependency, ExtractionResult
from app.services.manifest_parsers.common import infra_from_dep, norm_app, quote


def parse_csproj(text: str, path: str) -> ExtractionResult:
    name = Path(path).stem
    app_key = norm_app(name)
    claims = [
        ExtractedClaim(
            entity_type="application",
            entity_key=app_key,
            attribute="name",
            value=name,
            confidence=0.85,
            evidence_quote=quote(path, f"csproj {name}"),
            chunk_ids=[],
        ),
        ExtractedClaim(
            entity_type="application",
            entity_key=app_key,
            attribute="runtime",
            value="dotnet",
            confidence=0.9,
            evidence_quote=quote(path, ".csproj indicates .NET"),
            chunk_ids=[],
        ),
    ]
    deps: list[ExtractedDependency] = []
    extra: list[ExtractedClaim] = []
    for pkg in re.findall(r'Include="([^"]+)"', text):
        for item in infra_from_dep(pkg, app_key, path):
            if isinstance(item, ExtractedClaim):
                extra.append(item)
            else:
                deps.append(item)
    return ExtractionResult(claims=claims + extra, dependencies=deps)

def parse_appsettings(text: str, path: str) -> ExtractionResult:
    claims: list[ExtractedClaim] = []
    deps: list[ExtractedDependency] = []
    app_key = "dotnet-app"
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        data = {}
    conn = data.get("ConnectionStrings") or {}
    for key in conn:
        # do not store secret values
        claims.append(
            ExtractedClaim(
                entity_type="application",
                entity_key=app_key,
                attribute="connection_string_key",
                value=str(key),
                confidence=0.8,
                evidence_quote=quote(path, f"ConnectionStrings.{key} present (value redacted)"),
                chunk_ids=[],
            )
        )
        claims.append(
            ExtractedClaim(
                entity_type="application",
                entity_key=app_key,
                attribute="connection_string_present",
                value="true",
                confidence=0.85,
                evidence_quote=quote(path, f"ConnectionStrings.{key}=***"),
                chunk_ids=[],
            )
        )
        kl = key.lower()
        if "redis" in kl:
            for item in infra_from_dep("redis", app_key, path):
                if isinstance(item, ExtractedDependency):
                    deps.append(item)
                else:
                    claims.append(item)
        else:
            for item in infra_from_dep("postgres", app_key, path):
                if isinstance(item, ExtractedDependency):
                    deps.append(item)
                else:
                    claims.append(item)
    return ExtractionResult(claims=claims, dependencies=deps)

