"""Labeled eval estates (ground truth) for the eval harness.

The Contoso estate's facts are the ones planted by `scripts/generate_sample_data.py`;
these labels must stay in step with that generator (both describe the same fixtures under
`sample-data/`). Kept as data, separate from the scoring code.
"""

from __future__ import annotations

from app.services.eval_harness import EstateLabels, RetrievalProbe, SizingFact

_DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

CONTOSO = EstateLabels(
    name="contoso",
    files=(
        ("cmdb-inventory.xlsx", _XLSX),
        ("architecture-overview.docx", _DOCX),
        ("requirements-nfr.docx", _DOCX),
        ("assessment-questionnaire.docx", _DOCX),
        ("sample-app.zip", "application/zip"),
    ),
    expected_servers=frozenset({"app-portal-01", "app-bill-01", "app-id-01", "app-rpt-01"}),
    expected_applications=frozenset(
        {"customer-portal", "billing-service", "identity-gateway", "reporting-hub"}
    ),
    expected_databases=frozenset({"portaldb", "financedb", "authdb"}),
    sizing=(
        SizingFact("app-portal-01", vcpus=4, memory_gb=16, os="Windows Server 2019"),
        SizingFact("app-bill-01", vcpus=8, memory_gb=32, os="RHEL 8"),  # OS wins over legacy WS2016
        SizingFact("app-id-01", vcpus=4, memory_gb=16, os="Ubuntu 22"),
        SizingFact("app-rpt-01", os="Windows Server 2022"),  # vCPU/memory deliberately blank in source
    ),
    expected_nfr_attributes=frozenset(
        {
            "sla",
            "rto",
            "rpo",
            "availability",
            "latency",
            "compliance",
            "data_residency",
            "encryption",
            "scalability",
        }
    ),
    retrieval_probes=(
        RetrievalProbe(
            "servers hostnames operating systems infrastructure inventory",
            ("app-portal-01", "app-bill-01", "app-id-01", "app-rpt-01"),
        ),
        RetrievalProbe(
            "server vCPU memory utilization storage disk IOPS region architecture",
            ("app-bill-01", "32", "8"),
        ),
        RetrievalProbe(
            "databases data stores SQL Oracle PostgreSQL engines",
            ("FinanceDB", "PortalDB", "AuthDB"),
        ),
        RetrievalProbe(
            "non-functional requirements latency availability RTO RPO SLA",
            ("99.9", "RTO", "RPO"),
        ),
        RetrievalProbe(
            "total vCPU across all servers combined sum",
            ("sum=16",),  # the computed table-summary chunk
        ),
    ),
)

ALL_ESTATES = {CONTOSO.name: CONTOSO}
