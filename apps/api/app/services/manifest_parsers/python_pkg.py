from __future__ import annotations

import re

from app.schemas.api import ExtractedClaim, ExtractedDependency, ExtractionResult
from app.services.manifest_parsers.common import INFRA_DEPS, infra_from_dep, norm_app, quote


def parse_requirements_txt(text: str, path: str) -> ExtractionResult:
    app_key = "python-app"
    claims = [
        ExtractedClaim(
            entity_type="application",
            entity_key=app_key,
            attribute="name",
            value="Python App",
            confidence=0.7,
            evidence_quote=quote(path, "requirements.txt"),
            chunk_ids=[],
        ),
        ExtractedClaim(
            entity_type="application",
            entity_key=app_key,
            attribute="runtime",
            value="python",
            confidence=0.9,
            evidence_quote=quote(path, "requirements.txt indicates Python"),
            chunk_ids=[],
        ),
    ]
    deps: list[ExtractedDependency] = []
    extra: list[ExtractedClaim] = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        pkg = re.split(r"[=<>!~\[]", line, maxsplit=1)[0].strip()
        for item in infra_from_dep(pkg, app_key, path):
            if isinstance(item, ExtractedClaim):
                extra.append(item)
            else:
                deps.append(item)
        if pkg.lower() in {"fastapi", "django", "flask"}:
            claims.append(
                ExtractedClaim(
                    entity_type="application",
                    entity_key=app_key,
                    attribute="framework",
                    value=pkg.lower(),
                    confidence=0.88,
                    evidence_quote=quote(path, line[:80]),
                    chunk_ids=[],
                )
            )
    return ExtractionResult(claims=claims + extra, dependencies=deps)

def parse_pyproject(text: str, path: str) -> ExtractionResult:
    name_m = re.search(r'name\s*=\s*"([^"]+)"', text)
    name = name_m.group(1) if name_m else "python-app"
    app_key = norm_app(name)
    claims = [
        ExtractedClaim(
            entity_type="application",
            entity_key=app_key,
            attribute="name",
            value=name,
            confidence=0.85,
            evidence_quote=quote(path, f'name = "{name}"'),
            chunk_ids=[],
        ),
        ExtractedClaim(
            entity_type="application",
            entity_key=app_key,
            attribute="runtime",
            value="python",
            confidence=0.9,
            evidence_quote=quote(path, "pyproject.toml"),
            chunk_ids=[],
        ),
    ]
    deps: list[ExtractedDependency] = []
    extra: list[ExtractedClaim] = []
    for key in INFRA_DEPS:
        if key in text.lower():
            for item in infra_from_dep(key, app_key, path):
                if isinstance(item, ExtractedClaim):
                    extra.append(item)
                else:
                    deps.append(item)
    return ExtractionResult(claims=claims + extra, dependencies=deps)

