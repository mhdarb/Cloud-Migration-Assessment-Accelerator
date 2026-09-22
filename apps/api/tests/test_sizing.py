from app.models.entities import Chunk, Document, DocumentType, Server
from app.services.heuristic_extract import heuristic_extract
from app.services.pricing import load_catalog, price_vm
from app.services.sizing import generate_recommendations, normalize_workload, size_profile


def test_normalize_and_size_known_workload():
    server = Server(
        assessment_id="a",
        name="billing-01",
        normalized_key="billing-01",
        confidence=0.9,
        attributes={
            "vcpus": "8",
            "memory_gb": "32 GB",
            "cpu_utilization_pct": "50%",
            "memory_utilization_pct": "60%",
            "disk_gb": "900",
            "disk_iops": "3200",
            "disk_throughput_mbps": "170",
            "environment": "Prod",
            "os": "RHEL 8",
        },
    )
    result = size_profile(normalize_workload(server))
    assert result["recommended_sku"].startswith("Standard_")
    assert result["disk"]["capacity_gb"] >= 900
    assert result["pricing"]["monthly_total"] > 0
    assert result["assumptions"] == []
    assert result["needs_human_review"] is False
    assert result["sku_decision"] == "recommended"
    assert "vcpus" in result["measured_fields"]


def test_unsupported_os_does_not_recommend_sku():
    server = Server(
        assessment_id="a",
        name="aix-01",
        normalized_key="aix-01",
        confidence=0.9,
        attributes={"vcpus": "8", "memory_gb": "32", "os": "AIX 7.2"},
    )
    result = size_profile(normalize_workload(server))
    assert result["sku_decision"] == "blocked"
    assert result["recommended_sku"] == ""
    assert result["pricing"]["monthly_total"] == 0
    assert result["needs_human_review"] is True


def test_missing_utilization_uses_reviewable_assumptions():
    server = Server(
        assessment_id="a",
        name="unknown-01",
        normalized_key="unknown-01",
        confidence=0.8,
        attributes={"os": "Ubuntu 22"},
    )
    result = size_profile(normalize_workload(server))
    assert result["assumptions"]
    assert result["needs_human_review"] is True
    assert result["confidence"] < 0.8
    assert "vcpus" in result["assumed_fields"]
    assert "cpu_utilization_pct" in result["assumed_fields"]


def test_recommendations_are_persisted(db_session, assessment):
    db_session.add(
        Server(
            assessment_id=assessment.id,
            name="app-01",
            normalized_key="app-01",
            confidence=0.9,
            attributes={"vcpus": "2", "memory_gb": "8", "os": "Linux"},
        )
    )
    db_session.commit()
    rows = generate_recommendations(db_session, assessment.id)
    assert len(rows) == 1
    assert rows[0].result["catalog_version"]


def test_inventory_columns_become_cited_server_claims():
    doc = Document(id="doc-1", doc_type=DocumentType.inventory)
    chunk = Chunk(
        id="chunk-1",
        assessment_id="a",
        document_id="doc-1",
        chunk_index=0,
        text=(
            "Server | vCPU | Memory GB | CPU Utilization % | Disk IOPS\n"
            "app-01 | 4 | 16 | 52 | 1200"
        ),
    )
    result = heuristic_extract([chunk], {"doc-1": doc})
    values = {(c.attribute, c.value) for c in result.claims}
    assert ("vcpus", "4") in values
    assert ("memory_gb", "16") in values
    assert ("cpu_utilization_pct", "52") in values
    assert all(c.chunk_ids == ["chunk-1"] for c in result.claims)


def test_offline_pricing_has_provenance(monkeypatch):
    from app.config import get_settings

    monkeypatch.setenv("AZURE_PRICING_LIVE", "false")
    get_settings.cache_clear()
    vm = load_catalog()["vms"][0]
    price = price_vm(vm, "eastus", "USD")
    assert price["source"] == "local-catalog"
    assert price["estimate"] is True
    assert price["monthly"] > 0


def test_recommendation_api_shape(db_session, assessment):
    from app.routers.assessments import list_recommendations

    db_session.add(
        Server(
            assessment_id=assessment.id,
            name="api-01",
            normalized_key="api-01",
            confidence=0.9,
            attributes={"vcpus": "2", "memory_gb": "8", "os": "Linux"},
        )
    )
    db_session.commit()
    generate_recommendations(db_session, assessment.id)
    response = list_recommendations(assessment.id, db_session)
    assert response[0].recommended_sku
    assert response[0].result["pricing"]["monthly_total"] > 0


def test_report_serializes_recommendations_and_questions(db_session, assessment):
    from app.services.report import generate_report

    db_session.add(
        Server(
            assessment_id=assessment.id,
            name="report-01",
            normalized_key="report-01",
            confidence=0.9,
            attributes={"vcpus": "2", "memory_gb": "8", "os": "Linux"},
        )
    )
    db_session.commit()
    generate_recommendations(db_session, assessment.id)
    output = generate_report(db_session, assessment.id)
    assert output.report_json["infrastructure_recommendations"]
    assert output.report_json["assessment_questions"]["question_set"]
