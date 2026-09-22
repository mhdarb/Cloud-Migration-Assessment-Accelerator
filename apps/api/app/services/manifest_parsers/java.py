from __future__ import annotations

import xml.etree.ElementTree as ET

from app.schemas.api import ExtractedClaim, ExtractedDependency, ExtractionResult
from app.services.manifest_parsers.common import INFRA_DEPS, infra_from_dep, norm_app, quote


def parse_pom(text: str, path: str) -> ExtractionResult:
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return ExtractionResult()
    ns = ""
    if root.tag.startswith("{"):
        ns = root.tag.split("}")[0] + "}"

    def findtext(tag: str) -> str | None:
        el = root.find(f"{ns}{tag}")
        if el is not None and el.text:
            return el.text.strip()
        parent = root.find(f"{ns}artifactId")
        return parent.text.strip() if parent is not None and parent.text else None

    artifact = None
    art_el = root.find(f"{ns}artifactId")
    if art_el is not None and art_el.text:
        artifact = art_el.text.strip()
    name = artifact or "java-app"
    app_key = norm_app(name)
    claims = [
        ExtractedClaim(
            entity_type="application",
            entity_key=app_key,
            attribute="name",
            value=name,
            confidence=0.9,
            evidence_quote=quote(path, f"<artifactId>{name}</artifactId>"),
            chunk_ids=[],
        ),
        ExtractedClaim(
            entity_type="application",
            entity_key=app_key,
            attribute="runtime",
            value="java",
            confidence=0.9,
            evidence_quote=quote(path, "pom.xml indicates Java runtime"),
            chunk_ids=[],
        ),
    ]
    deps: list[ExtractedDependency] = []
    extra: list[ExtractedClaim] = []
    pom_text_l = text.lower()
    if "spring-boot" in pom_text_l:
        claims.append(
            ExtractedClaim(
                entity_type="application",
                entity_key=app_key,
                attribute="framework",
                value="spring-boot",
                confidence=0.9,
                evidence_quote=quote(path, "spring-boot dependency"),
                chunk_ids=[],
            )
        )
    for key in INFRA_DEPS:
        if key in pom_text_l:
            for item in infra_from_dep(key, app_key, path):
                if isinstance(item, ExtractedClaim):
                    extra.append(item)
                else:
                    deps.append(item)
    return ExtractionResult(claims=claims + extra, dependencies=deps)

def parse_gradle(text: str, path: str) -> ExtractionResult:
    app_key = "java-app"
    claims = [
        ExtractedClaim(
            entity_type="application",
            entity_key=app_key,
            attribute="name",
            value="Java App",
            confidence=0.7,
            evidence_quote=quote(path, "build.gradle"),
            chunk_ids=[],
        ),
        ExtractedClaim(
            entity_type="application",
            entity_key=app_key,
            attribute="runtime",
            value="java",
            confidence=0.85,
            evidence_quote=quote(path, "build.gradle"),
            chunk_ids=[],
        ),
    ]
    deps: list[ExtractedDependency] = []
    extra: list[ExtractedClaim] = []
    if "spring-boot" in text.lower():
        claims.append(
            ExtractedClaim(
                entity_type="application",
                entity_key=app_key,
                attribute="framework",
                value="spring-boot",
                confidence=0.88,
                evidence_quote=quote(path, "spring-boot"),
                chunk_ids=[],
            )
        )
    for key in INFRA_DEPS:
        if key in text.lower():
            for item in infra_from_dep(key, app_key, path):
                if isinstance(item, ExtractedClaim):
                    extra.append(item)
                else:
                    deps.append(item)
    return ExtractionResult(claims=claims + extra, dependencies=deps)

