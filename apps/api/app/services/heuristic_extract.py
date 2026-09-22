from __future__ import annotations

import re
from typing import Any

from app.models.entities import Chunk, Document, DocumentType
from app.schemas.api import ExtractedClaim, ExtractedDependency, ExtractionResult
from app.schemas.chunking import ChunkPayload


def chunks_from_payload(
    payload: list[ChunkPayload], assessment_id: str = ""
) -> list[Chunk]:
    return [
        Chunk(
            id=item["chunk_id"],
            assessment_id=assessment_id,
            document_id=item.get("document_id") or "",
            chunk_index=0,
            page=item.get("page") or 1,
            offset_start=0,
            offset_end=len(item.get("text") or ""),
            text=item.get("text") or "",
        )
        for item in payload
    ]


def chunks_to_payload(chunks: list[Chunk], text_limit_tokens: int = 400) -> list[dict[str, Any]]:
    from app.services.chunkers import _get_encoding, _window_by_tokens

    enc = _get_encoding()
    payload: list[dict[str, Any]] = []
    for chunk in chunks:
        text = chunk.text or ""
        windows = _window_by_tokens(text, text_limit_tokens, 0, enc)
        item: dict[str, Any] = {
            "chunk_id": chunk.id,
            "document_id": chunk.document_id,
            "page": chunk.page,
            "text": windows[0] if windows else text,
        }
        meta = getattr(chunk, "metadata_json", None) or {}
        for key in ("section_title", "row_range", "qa_index", "file_path"):
            if key in meta:
                item[key] = meta[key]
        payload.append(item)
    return payload


def make_claim(
    entity_type: str,
    entity_key: str,
    attribute: str,
    value: str,
    *,
    chunk_id: str,
    quote: str,
    confidence: float = 0.78,
) -> ExtractedClaim:
    return ExtractedClaim(
        entity_type=entity_type,
        entity_key=entity_key,
        attribute=attribute,
        value=value,
        confidence=confidence,
        evidence_quote=quote[:240],
        chunk_ids=[chunk_id],
    )

STOPWORDS = {
    "the",
    "and",
    "for",
    "with",
    "from",
    "application",
    "applications",
    "service",
    "server",
    "database",
}

_OS_CELL = re.compile(r"Windows Server|RHEL|Ubuntu|Linux|AIX", re.I)
_APP_PATTERN = re.compile(
    r"Application:\s*([A-Za-z0-9][A-Za-z0-9 /_-]{1,40}?)(?:\s*[—–-]|\s+is\b|\s+—|\.|,)",
    re.I,
)
_SERVER_HOST_PATTERN = re.compile(r"Server:\s*([a-z0-9][a-z0-9\-_.]{1,40})", re.I)
_DB_LABEL_PATTERN = re.compile(
    r"Database:\s*([A-Za-z0-9][A-Za-z0-9_-]{1,40})(?:\s*\(([^)]+)\))?",
    re.I,
)
_DEPENDS_PATTERN = re.compile(
    r"([A-Z][A-Za-z0-9]+(?:\s+[A-Z][A-Za-z0-9]+)?)\s+"
    r"(depends on|calls|integrates with)\s+"
    r"([A-Z][A-Za-z0-9]+(?:\s+[A-Z][A-Za-z0-9]+)?)",
)
_HOSTED_PATTERN = re.compile(
    r"([A-Z][A-Za-z0-9]+(?:\s+[A-Z][A-Za-z0-9]+)?)\s+is hosted on server\s+([a-z0-9][a-z0-9\-_.]+)",
    re.I,
)
_USES_DB_PATTERN = re.compile(
    r"([A-Z][A-Za-z0-9]+(?:\s+[A-Z][A-Za-z0-9]+)?)\s+uses\s+(?:database\s+)?([A-Za-z0-9][A-Za-z0-9_-]+)",
    re.I,
)
_OS_ON_SERVER = re.compile(
    r"([a-z0-9][a-z0-9\-_.]+)\s+runs\s+(Windows Server \d+|RHEL \d+|Ubuntu \d+|Linux|AIX)",
    re.I,
)
_NFR_PATTERNS = [
    (re.compile(r"NFR:\s*(.+)", re.I), "nfr"),
    (re.compile(r"SLA:\s*(.+)", re.I), "sla"),
    (re.compile(r"availability[^.\n]{0,40}?(\d{2,3}\.?\d*%|99\.9+%)", re.I), "availability"),
    (re.compile(r"RTO[:\s]+([^\n,]{2,40})", re.I), "rto"),
    (re.compile(r"RPO[:\s]+([^\n,]{2,40})", re.I), "rpo"),
    (re.compile(r"latency[^.\n]{0,40}?(\d+\s*ms)", re.I), "latency"),
    (re.compile(r"data residency[:\s]+([^\n.]{2,60})", re.I), "data_residency"),
    (re.compile(r"must support[:\s]+([^\n.]{2,80})", re.I), "nfr"),
    (re.compile(r"(PCI(?:-DSS)?|HIPAA|SOC\s*2|ISO\s*27001)", re.I), "compliance"),
    (re.compile(r"encrypt(?:ion|ed)[:\s]+([^\n.]{2,60})", re.I), "encryption"),
    (re.compile(r"peak load[:\s]+([^\n.]{2,60})", re.I), "scalability"),
    (re.compile(r"concurrent users[:\s]+([^\n.]{2,40})", re.I), "scalability"),
]
_CRITICALITY_LEVEL = re.compile(r"\b(high|medium|low)\b", re.I)

def normalize_key(name: str) -> str:
    key = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return key or "unknown"


def clean_name(name: str, max_words: int = 4) -> str | None:
    name = name.strip(" .-|:,;")
    name = re.split(r"\s+[—–-]\s+|\s+for\s+|\s+and\s+|\s+that\s+", name, maxsplit=1)[0]
    name = name.strip(" .-|:,;")
    words = name.split()
    if not name or len(name) < 2 or len(words) > max_words:
        return None
    if name.lower() in STOPWORDS:
        return None
    return name


INFRA_COLUMN_ALIASES = {
    "server": {"server", "hostname", "host", "server_name"},
    "vcpus": {"vcpus", "vcpu", "cpu_cores", "cores"},
    "memory_gb": {"memory_gb", "memory", "ram_gb", "ram"},
    "cpu_utilization_pct": {"cpu_utilization_pct", "cpu_utilization", "avg_cpu_pct", "cpu_pct"},
    "memory_utilization_pct": {
        "memory_utilization_pct",
        "memory_utilization",
        "avg_memory_pct",
        "memory_pct",
    },
    "disk_gb": {"disk_gb", "storage_gb", "disk_capacity_gb", "storage"},
    "disk_iops": {"disk_iops", "iops"},
    "disk_throughput_mbps": {"disk_throughput_mbps", "throughput_mbps"},
    "environment": {"environment", "env"},
    "region": {"region", "location"},
    "architecture": {"architecture", "arch"},
}


def _canonical_header(value: str) -> str:
    key = re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_")
    for canonical, aliases in INFRA_COLUMN_ALIASES.items():
        if key in aliases:
            return canonical
    return key


def _extract_inventory_columns(chunk: Chunk) -> list[ExtractedClaim]:
    lines = [line for line in chunk.text.splitlines() if "|" in line]
    if not lines:
        return []
    headers = [_canonical_header(c) for c in lines[0].split("|")]
    if "server" not in headers:
        return []
    server_idx = headers.index("server")
    supported = set(INFRA_COLUMN_ALIASES) - {"server"}
    claims: list[ExtractedClaim] = []
    for line in lines[1:]:
        cells = [c.strip() for c in line.split("|")]
        if server_idx >= len(cells) or not cells[server_idx]:
            continue
        server = cells[server_idx]
        for idx, attribute in enumerate(headers):
            if attribute not in supported or idx >= len(cells) or not cells[idx]:
                continue
            claims.append(
                make_claim(
                    "server",
                    normalize_key(server),
                    attribute,
                    cells[idx],
                    chunk_id=chunk.id,
                    quote=line,
                    confidence=0.9,
                )
            )
    return claims


def _extract_criticality_claims(
    hits: list[tuple[str, str]], app_names: set[str]
) -> list[ExtractedClaim]:
    """Generic criticality extractor (no hardcoded entity names): finds sentences
    mentioning "critical" that also name an already-discovered application, and pulls
    an explicit high/medium/low level from the same sentence when stated."""
    if not app_names:
        return []
    claims: list[ExtractedClaim] = []
    seen: set[str] = set()
    for chunk_id, text in hits:
        for sentence in re.split(r"(?<=[.!?])\s+", text):
            lower = sentence.lower()
            if "critical" not in lower:
                continue
            for name in app_names:
                key = normalize_key(name)
                if key in seen or name.lower() not in lower:
                    continue
                level_match = _CRITICALITY_LEVEL.search(sentence)
                value = level_match.group(1).lower() if level_match else "high"
                seen.add(key)
                claims.append(
                    make_claim(
                        "application",
                        key,
                        "business_criticality",
                        value,
                        chunk_id=chunk_id,
                        quote=sentence.strip(),
                        confidence=0.75 if level_match else 0.6,
                    )
                )
    return claims


def heuristic_extract(chunks: list[Chunk], docs: dict[str, Document]) -> ExtractionResult:
    """Structured + labeled-pattern extractor used for offline RAG demos."""
    claims: list[ExtractedClaim] = []
    deps: list[ExtractedDependency] = []
    gaps: list[str] = []
    assumptions: list[str] = []

    app_names: set[str] = set()
    server_names: set[str] = set()
    db_names: set[str] = set()
    criticality_hits: list[tuple[str, str]] = []

    for chunk in chunks:
        text = chunk.text
        doc = docs.get(chunk.document_id)
        is_inventory = bool(doc and doc.doc_type == DocumentType.inventory)
        conf_base = 0.9 if is_inventory else 0.75
        if is_inventory:
            claims.extend(_extract_inventory_columns(chunk))

        for line in text.splitlines():
            cells = [c.strip() for c in line.split("|")]
            if not cells or cells[0].startswith("#"):
                continue
            if cells[0].lower() in {
                "application",
                "app",
                "name",
                "server",
                "hostname",
                "database",
                "source",
            }:
                continue

            if len(cells) >= 2 and re.match(r"^[a-z0-9][a-z0-9\-_.]+$", cells[0], re.I):
                if _OS_CELL.search(cells[1]):
                    server = cells[0]
                    server_names.add(server)
                    claims.append(
                        make_claim(
                            "server",
                            normalize_key(server),
                            "name",
                            server,
                            chunk_id=chunk.id,
                            quote=line,
                            confidence=0.8,
                        )
                    )
                    claims.append(
                        make_claim(
                            "server",
                            normalize_key(server),
                            "os",
                            cells[1],
                            chunk_id=chunk.id,
                            quote=line,
                            confidence=0.55 if "legacy" in text.lower() else 0.8,
                        )
                    )
                    continue

            if len(cells) >= 3 and (is_inventory or len(cells) >= 5):
                app, server, database = cells[0], cells[1], cells[2]
                app_c = clean_name(app)
                if not app_c or not re.match(r"^[a-z0-9]", server, re.I):
                    continue
                app_names.add(app_c)
                server_names.add(server)
                claims.append(
                    make_claim(
                        "application",
                        normalize_key(app_c),
                        "name",
                        app_c,
                        chunk_id=chunk.id,
                        quote=line,
                        confidence=conf_base,
                    )
                )
                claims.append(
                    make_claim(
                        "server",
                        normalize_key(server),
                        "name",
                        server,
                        chunk_id=chunk.id,
                        quote=line,
                        confidence=conf_base,
                    )
                )
                deps.append(
                    ExtractedDependency(
                        source_type="application",
                        source_key=normalize_key(app_c),
                        target_type="server",
                        target_key=normalize_key(server),
                        relationship="hosted_on",
                        confidence=conf_base,
                        evidence_quote=line[:240],
                        chunk_ids=[chunk.id],
                    )
                )
                if database and clean_name(database, max_words=2):
                    db_c = clean_name(database, max_words=2)
                    assert db_c
                    db_names.add(db_c)
                    claims.append(
                        make_claim(
                            "database",
                            normalize_key(db_c),
                            "name",
                            db_c,
                            chunk_id=chunk.id,
                            quote=line,
                            confidence=conf_base,
                        )
                    )
                    deps.append(
                        ExtractedDependency(
                            source_type="application",
                            source_key=normalize_key(app_c),
                            target_type="database",
                            target_key=normalize_key(db_c),
                            relationship="uses",
                            confidence=conf_base,
                            evidence_quote=line[:240],
                            chunk_ids=[chunk.id],
                        )
                    )
                if len(cells) >= 5 and _OS_CELL.search(cells[4]):
                    claims.append(
                        make_claim(
                            "server",
                            normalize_key(server),
                            "os",
                            cells[4],
                            chunk_id=chunk.id,
                            quote=line,
                            confidence=conf_base,
                        )
                    )
                if len(cells) >= 4 and cells[3]:
                    claims.append(
                        make_claim(
                            "application",
                            normalize_key(app_c),
                            "tier",
                            cells[3],
                            chunk_id=chunk.id,
                            quote=line,
                            confidence=conf_base,
                        )
                    )

        # NFR / requirements heuristics (requirements docs + general prose)
        if doc and doc.doc_type in {DocumentType.requirements, DocumentType.architecture, DocumentType.questionnaire}:
            claims.extend(extract_nfr_claims(text, chunk.id))

        # Criticality/PCI/HIPAA/gap signals are generic substring checks that can apply
        # to any doc type (e.g. a CMDB "Notes" column mentioning PCI) — run them before
        # the inventory `continue` below, which only exists to skip the *prose* regex
        # patterns that follow (those genuinely don't apply to structured tables).
        lower = text.lower()
        if "critical" in lower:
            criticality_hits.append((chunk.id, text))
        if "pci" in lower:
            gaps.append("PCI-relevant data identified; confirm in-region residency requirements")
        if "hipaa" in lower:
            gaps.append("HIPAA-related controls referenced; validate cloud control mapping")
        if "not documented" in lower or "known gaps" in lower:
            gaps.append("Network topology and firewall rules not present in uploaded pack")

        if doc and doc.doc_type == DocumentType.inventory:
            continue

        for m in _APP_PATTERN.finditer(text):
            name = clean_name(m.group(1))
            if not name:
                continue
            app_names.add(name)
            claims.append(
                make_claim(
                    "application",
                    normalize_key(name),
                    "name",
                    name,
                    chunk_id=chunk.id,
                    quote=m.group(0),
                )
            )
        for m in _SERVER_HOST_PATTERN.finditer(text):
            name = m.group(1).rstrip(".,;")
            server_names.add(name)
            claims.append(
                make_claim(
                    "server",
                    normalize_key(name),
                    "name",
                    name,
                    chunk_id=chunk.id,
                    quote=m.group(0),
                )
            )
        for m in _DB_LABEL_PATTERN.finditer(text):
            name = clean_name(m.group(1), max_words=2)
            if not name:
                continue
            db_names.add(name)
            claims.append(
                make_claim(
                    "database",
                    normalize_key(name),
                    "name",
                    name,
                    chunk_id=chunk.id,
                    quote=m.group(0),
                )
            )
            if m.group(2):
                claims.append(
                    make_claim(
                        "database",
                        normalize_key(name),
                        "engine",
                        m.group(2).strip(),
                        chunk_id=chunk.id,
                        quote=m.group(0),
                        confidence=0.75,
                    )
                )
        for m in _OS_ON_SERVER.finditer(text):
            server, os_val = m.group(1), m.group(2)
            server_names.add(server)
            claims.append(
                make_claim(
                    "server",
                    normalize_key(server),
                    "os",
                    os_val,
                    chunk_id=chunk.id,
                    quote=m.group(0),
                    confidence=0.8,
                )
            )
        for m in _HOSTED_PATTERN.finditer(text):
            app, server = clean_name(m.group(1)), m.group(2)
            if not app:
                continue
            deps.append(
                ExtractedDependency(
                    source_type="application",
                    source_key=normalize_key(app),
                    target_type="server",
                    target_key=normalize_key(server),
                    relationship="hosted_on",
                    confidence=0.8,
                    evidence_quote=m.group(0)[:240],
                    chunk_ids=[chunk.id],
                )
            )
        for m in _USES_DB_PATTERN.finditer(text):
            app, database = clean_name(m.group(1)), clean_name(m.group(2), max_words=2)
            if not app or not database:
                continue
            if database.lower() in {"oracle", "sql", "postgres", "mysql"}:
                continue
            deps.append(
                ExtractedDependency(
                    source_type="application",
                    source_key=normalize_key(app),
                    target_type="database",
                    target_key=normalize_key(database),
                    relationship="uses",
                    confidence=0.75,
                    evidence_quote=m.group(0)[:240],
                    chunk_ids=[chunk.id],
                )
            )
        for m in _DEPENDS_PATTERN.finditer(text):
            src, rel, tgt = clean_name(m.group(1)), m.group(2).lower(), clean_name(m.group(3))
            if not src or not tgt:
                continue
            deps.append(
                ExtractedDependency(
                    source_type="application",
                    source_key=normalize_key(src),
                    target_type="application",
                    target_key=normalize_key(tgt),
                    relationship="depends_on" if "depend" in rel else "calls",
                    confidence=0.72,
                    evidence_quote=m.group(0)[:240],
                    chunk_ids=[chunk.id],
                )
            )

    claims.extend(_extract_criticality_claims(criticality_hits, app_names))

    if not app_names:
        gaps.append("No applications identified in source documents")
    if not server_names:
        gaps.append("No servers identified in source documents")
    if not db_names:
        gaps.append("No databases identified in source documents")

    return ExtractionResult(
        claims=claims,
        dependencies=deps,
        gaps=list(dict.fromkeys(gaps)),
        assumptions=assumptions
        or ["Extracted via RAG heuristic; validate before migration planning"],
    )


def extract_nfr_claims(text: str, chunk_id: str) -> list[ExtractedClaim]:
    """Offline NFR/SLA/compliance extractors for requirements-style prose."""
    out: list[ExtractedClaim] = []
    for pattern, attr in _NFR_PATTERNS:
        for m in pattern.finditer(text):
            value = m.group(1).strip(" .;:")
            if len(value) < 2:
                continue
            out.append(
                make_claim(
                    "business",
                    "migration-requirements",
                    attr,
                    value[:200],
                    chunk_id=chunk_id,
                    quote=m.group(0),
                )
            )
    return out
