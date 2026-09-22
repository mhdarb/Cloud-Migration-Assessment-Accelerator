from app.schemas.extraction_targets import (
    DependencyClaim,
    DependencyTarget,
    NfrClaim,
    NfrTarget,
    ServerClaim,
    ServerSizingTarget,
)


def test_server_sizing_target_adapts_to_extraction_result():
    target = ServerSizingTarget(
        servers=[
            ServerClaim(
                server_key="app-bill-01",
                vcpu=8,
                memory_gb=32,
                os="RHEL 8",
                evidence_quote="app-bill-01 runs RHEL 8 with 8 vCPU and 32GB RAM",
                chunk_ids=["c1"],
                confidence=0.85,
            )
        ],
        gaps=["disk IOPS not stated"],
    )
    result = target.to_extraction_result()
    attrs = {(c.entity_type, c.entity_key, c.attribute, c.value) for c in result.claims}
    assert ("server", "app-bill-01", "vcpu", "8") in attrs
    assert ("server", "app-bill-01", "memory_gb", "32.0") in attrs
    assert ("server", "app-bill-01", "os", "RHEL 8") in attrs
    assert result.gaps == ["disk IOPS not stated"]
    assert all(c.chunk_ids == ["c1"] for c in result.claims)


def test_server_sizing_target_skips_none_fields():
    target = ServerSizingTarget(
        servers=[ServerClaim(server_key="s1", vcpu=4, evidence_quote="q", chunk_ids=["c1"])]
    )
    result = target.to_extraction_result()
    assert len(result.claims) == 1
    assert result.claims[0].attribute == "vcpu"


def test_nfr_target_adapts_to_business_claims():
    target = NfrTarget(
        requirements=[
            NfrClaim(attribute="rto", value="4 hours", evidence_quote="RTO: 4 hours", chunk_ids=["c1"]),
            NfrClaim(attribute="not_a_real_attr", value="x", evidence_quote="q", chunk_ids=["c2"]),
        ]
    )
    result = target.to_extraction_result()
    assert len(result.claims) == 2
    assert all(c.entity_type == "business" for c in result.claims)
    assert result.claims[0].attribute == "rto"
    # Unknown attributes normalize to "nfr" rather than passing through arbitrary strings.
    assert result.claims[1].attribute == "nfr"


def test_dependency_target_adapts_to_dependencies():
    target = DependencyTarget(
        edges=[
            DependencyClaim(
                source_key="customer-portal",
                source_type="application",
                target_key="billing-service",
                target_type="application",
                relationship="depends_on",
                evidence_quote="Customer Portal depends on Billing Service",
                chunk_ids=["c1"],
            )
        ]
    )
    result = target.to_extraction_result()
    assert len(result.dependencies) == 1
    dep = result.dependencies[0]
    assert dep.source_key == "customer-portal"
    assert dep.target_key == "billing-service"
    assert dep.relationship == "depends_on"
    assert result.claims == []
