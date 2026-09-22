from app.schemas.api import ExtractedClaim, ExtractedDependency, ExtractionResult
from app.services.citations import quote_grounded, validate_citations


def test_quote_grounded_accepts_substring():
    assert quote_grounded(
        "Billing Service is hosted on",
        ["Application: Billing Service is hosted on server app-01."],
    )


def test_quote_grounded_strips_manifest_path_prefix():
    chunk = '# FILE: package.json\n{\n  "name": "orders-api",\n  "dependencies": {"pg": "8.0"}\n}'
    assert quote_grounded('[package.json] "name": "orders-api"', [chunk])


def test_quote_grounded_rejects_fabricated():
    assert not quote_grounded(
        "totally invented quote about mars",
        ["Application: Billing Service uses Oracle."],
    )


def test_validate_citations_clears_ungrounded():
    result = ExtractionResult(
        claims=[
            ExtractedClaim(
                entity_type="application",
                entity_key="billing",
                attribute="name",
                value="Billing",
                confidence=0.9,
                evidence_quote="Billing Service is hosted",
                chunk_ids=["c1"],
            ),
            ExtractedClaim(
                entity_type="application",
                entity_key="fake",
                attribute="name",
                value="Fake",
                confidence=0.9,
                evidence_quote="this quote is not in any chunk",
                chunk_ids=["c1"],
            ),
        ],
        dependencies=[
            ExtractedDependency(
                source_type="application",
                source_key="billing",
                target_type="server",
                target_key="app-01",
                relationship="hosted_on",
                confidence=0.9,
                evidence_quote="invented dependency quote",
                chunk_ids=["c1"],
            )
        ],
    )
    texts = {"c1": "Application: Billing Service is hosted on server app-01."}
    out = validate_citations(result, {"c1"}, texts)
    assert out.claims[0].chunk_ids == ["c1"]
    assert out.claims[1].chunk_ids == []
    assert out.claims[1].confidence <= 0.35
    assert out.dependencies[0].chunk_ids == []
