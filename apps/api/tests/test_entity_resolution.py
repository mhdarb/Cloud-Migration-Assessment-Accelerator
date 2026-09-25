"""Entity resolution: de-noising chat-model extraction output.

The central test replays extraction output spelled the way a real chat model drifts across
independent skill calls, and checks the materialized entities come out clean. Before this
module existed that same input produced 6 applications (4 real), 4 servers (3 real, one of
them lost), 4 databases (3 real, one of them lost) and 2 of 4 dependency edges.
"""

from __future__ import annotations

import pytest

from app.models.entities import Application, DatabaseEntity, DependencyEdge, Server
from app.schemas.api import ExtractedClaim, ExtractedDependency, ExtractionResult
from app.services.entity_resolution import normalize_extraction
from app.services.extraction_merge import dedupe_extraction
from app.services.guardrails import sanitize_extraction_output
from app.services.normalization import (
    canonical_attribute,
    canonical_entity_key,
    canonical_entity_type,
    is_generic_entity_key,
)
from app.services.reconciliation import persist_extraction


def _c(t, k, a, v):
    return ExtractedClaim(
        entity_type=t, entity_key=k, attribute=a, value=v, confidence=0.85, evidence_quote=v, chunk_ids=["c1"]
    )


def _d(st, sk, tt, tk, rel="depends_on"):
    return ExtractedDependency(
        source_type=st, source_key=sk, target_type=tt, target_key=tk, relationship=rel,
        confidence=0.8, evidence_quote="x", chunk_ids=["c1"],
    )


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "raw,expected",
    [("vm", "server"), ("Host", "server"), ("db", "database"), ("Service", "application"),
     ("API", "interface"), ("NFR", "business"), ("application", "application"), ("malware", None)],
)
def test_canonical_entity_type(raw, expected):
    assert canonical_entity_type(raw) == expected


def test_canonical_entity_key():
    assert canonical_entity_key("application", "Billing Service") == "billing-service"
    assert canonical_entity_key("application", "billing_service") == "billing-service"
    assert canonical_entity_key("application", "The Billing Service") == "billing-service"
    assert canonical_entity_key("server", "APP-BILL-01.contoso.local") == "app-bill-01"
    assert canonical_entity_key("server", "10.1.0.10") == "10-1-0-10"


@pytest.mark.parametrize(
    "raw,expected",
    [("vCPU", "vcpus"), ("CPU Cores", "vcpus"), ("RAM (GB)", "memory_gb"), ("Operating System", "os"),
     ("hostname", "name"), ("Criticality", "business_criticality"), ("DB Engine", "engine"),
     ("rto", "rto"), ("Data Residency", "data_residency")],
)
def test_canonical_attribute(raw, expected):
    assert canonical_attribute(raw) == expected


def test_generic_keys_exclude_plausible_service_names():
    assert is_generic_entity_key("application", "the-application")
    assert is_generic_entity_key("server", "unknown")
    # Short names that are real docker-compose services must survive.
    for real in ("api", "app", "db", "web"):
        assert not is_generic_entity_key("application", real)
    assert not is_generic_entity_key("business", "migration-requirements")


# --------------------------------------------------------------------------- #
# Resolution behaviour
# --------------------------------------------------------------------------- #
def test_spelling_variants_merge_to_the_name_derived_key():
    result, stats = normalize_extraction(
        ExtractionResult(
            claims=[
                _c("application", "billing-service", "name", "Billing Service"),
                _c("application", "BillingService", "tier", "App"),
                _c("database", "auth-db", "name", "AuthDB"),
                _c("database", "authdb", "engine", "PostgreSQL"),
            ]
        )
    )
    keys = {(c.entity_type, c.entity_key) for c in result.claims}
    assert keys == {("application", "billing-service"), ("database", "authdb")}
    assert stats.variants_merged == 2


def test_servers_are_not_merged_on_separators():
    """web-1-2 and web-12 can be different machines; only case/FQDN are folded."""
    result, _ = normalize_extraction(
        ExtractionResult(claims=[_c("server", "web-1-2", "os", "RHEL"), _c("server", "web-12", "os", "RHEL")])
    )
    assert {c.entity_key for c in result.claims} == {"web-1-2", "web-12"}


def test_dependencies_follow_merged_keys():
    result, _ = normalize_extraction(
        ExtractionResult(
            claims=[_c("application", "billing-service", "name", "Billing Service")],
            dependencies=[_d("application", "Customer Portal", "service", "BillingService")],
        )
    )
    dep = result.dependencies[0]
    assert (dep.source_key, dep.target_type, dep.target_key) == ("customer-portal", "application", "billing-service")


def test_normalization_is_idempotent():
    raw = ExtractionResult(
        claims=[_c("vm", "APP-01.corp", "vCPU", "8"), _c("application", "Billing_Service", "name", "Billing Service")]
    )
    once, _ = normalize_extraction(raw)
    twice, stats = normalize_extraction(once)
    assert once == twice
    assert stats.keys_rewritten == stats.types_mapped == stats.attributes_mapped == 0


def test_dedupe_collapses_equal_measurements():
    out = dedupe_extraction(
        ExtractionResult(claims=[_c("server", "app-01", "vcpus", "8"), _c("server", "app-01", "vCPU", "8.0")])
    )
    assert len(out.claims) == 1


def test_sanitizer_maps_synonym_types_instead_of_dropping(monkeypatch):
    from app.config import get_settings

    monkeypatch.setenv("GUARDRAILS_ENABLED", "true")
    get_settings.cache_clear()
    out, _ = sanitize_extraction_output(ExtractionResult(claims=[_c("vm", "app-01", "os", "RHEL 9")]))
    assert [(c.entity_type, c.entity_key) for c in out.claims] == [("server", "app-01")]


# --------------------------------------------------------------------------- #
# End to end: noisy multi-skill LLM output -> clean entities
# --------------------------------------------------------------------------- #
def _noisy_llm_skill_outputs() -> list[ExtractionResult]:
    return [
        ExtractionResult(claims=[  # applications skill
            _c("application", "billing-service", "name", "Billing Service"),
            _c("application", "customer-portal", "name", "Customer Portal"),
            _c("application", "identity-gateway", "name", "Identity Gateway"),
            _c("application", "reporting-hub", "name", "Reporting Hub"),
            _c("application", "the-application", "name", "the application"),
            _c("application", "N/A", "name", "N/A"),
        ]),
        ExtractionResult(claims=[  # servers skill, drifting types
            _c("server", "app-bill-01", "os", "RHEL 8"),
            _c("vm", "app-portal-01", "os", "Windows Server 2019"),
            _c("host", "app-id-01", "os", "Ubuntu 22"),
            _c("server", "unknown", "os", "Linux"),
        ]),
        ExtractionResult(claims=[  # sizing skill + generic-schema attribute drift
            _c("server", "app-bill-01", "vcpu", "8"),
            _c("server", "APP-BILL-01", "memory_gb", "32"),
            _c("server", "app-bill-01.contoso.local", "Operating System", "RHEL 8"),
            _c("server", "app-portal-01", "vCPU", "4.0"),
            _c("server", "app-portal-01", "RAM (GB)", "16"),
            _c("server", "app-portal-01", "vcpus", "4"),
        ]),
        ExtractionResult(claims=[  # databases skill
            _c("database", "FinanceDB", "name", "FinanceDB"),
            _c("database", "finance_db", "engine", "Oracle"),
            _c("db", "PortalDB", "name", "PortalDB"),
            _c("database", "auth-db", "name", "AuthDB"),
            _c("database", "authdb", "engine", "PostgreSQL"),
        ]),
        ExtractionResult(  # dependency / integration skills
            claims=[_c("service", "BillingService", "business_criticality", "high")],
            dependencies=[
                _d("application", "Customer Portal", "application", "BillingService"),
                _d("application", "billing_service", "server", "APP-BILL-01", "hosted_on"),
                _d("service", "billing-service", "application", "identity-gateway", "calls"),
                _d("application", "billing-service", "db", "FinanceDB", "uses"),
            ],
        ),
    ]


def test_noisy_llm_output_materializes_clean_entities(db_session, assessment, monkeypatch):
    from app.config import get_settings

    monkeypatch.setenv("GUARDRAILS_ENABLED", "true")
    get_settings.cache_clear()

    merged = ExtractionResult()
    for skill_output in _noisy_llm_skill_outputs():  # per-query sanitize, as the agent does
        out, _ = sanitize_extraction_output(skill_output)
        merged.claims += out.claims
        merged.dependencies += out.dependencies
    persist_extraction(db_session, assessment.id, dedupe_extraction(merged))

    def keys(model):
        return {r.normalized_key for r in db_session.query(model).filter(model.assessment_id == assessment.id)}

    assert keys(Application) == {"billing-service", "customer-portal", "identity-gateway", "reporting-hub"}
    assert keys(Server) == {"app-bill-01", "app-portal-01", "app-id-01"}  # app-id-01 was lost before
    assert keys(DatabaseEntity) == {"financedb", "portaldb", "authdb"}  # portaldb was lost before

    attrs = {s.normalized_key: set(s.attributes or {}) for s in db_session.query(Server)}
    assert attrs["app-bill-01"] == {"vcpus", "memory_gb", "os"}
    assert attrs["app-portal-01"] == {"vcpus", "memory_gb", "os"}

    edges = {
        (e.source_key, e.rel_type, e.target_key)
        for e in db_session.query(DependencyEdge).filter(DependencyEdge.assessment_id == assessment.id)
    }
    assert edges == {
        ("customer-portal", "depends_on", "billing-service"),
        ("billing-service", "hosted_on", "app-bill-01"),
        ("billing-service", "calls", "identity-gateway"),
        ("billing-service", "uses", "financedb"),
    }
