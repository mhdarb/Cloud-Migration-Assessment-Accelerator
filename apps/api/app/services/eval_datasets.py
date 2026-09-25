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

MERIDIAN = EstateLabels(
    name="meridian",
    subdir="eval/meridian",
    files=(
        ("server-inventory.csv", "text/csv"),
        ("architecture.docx", _DOCX),
        ("requirements.docx", _DOCX),
    ),
    expected_servers=frozenset({"mh-web-01", "mh-web-02", "mh-app-01", "mh-db-01"}),
    expected_applications=frozenset({"patient-portal", "records-api", "scheduler"}),
    expected_databases=frozenset({"recordsdb", "portaldb"}),
    sizing=(
        # Memory is given in MB *in the header* ("RAM (MB)") — canonical values are GB.
        SizingFact("mh-web-01", vcpus=4, memory_gb=16, disk_gb=200, os="CentOS 7"),
        SizingFact("mh-web-02", vcpus=4, memory_gb=16, disk_gb=200, os="CentOS 7"),
        SizingFact("mh-app-01", vcpus=8, memory_gb=32, disk_gb=500, os="RHEL 9"),
        SizingFact("mh-db-01", vcpus=16, memory_gb=64, disk_gb=2000, os="Debian 12"),
    ),
    expected_nfr_attributes=frozenset(
        {"compliance", "rto", "rpo", "availability", "latency", "encryption", "data_residency", "scalability"}
    ),
    retrieval_probes=(
        RetrievalProbe(
            "servers hostnames operating systems infrastructure inventory",
            ("mh-web-01", "mh-app-01", "mh-db-01"),
        ),
        RetrievalProbe(
            "server vCPU memory storage disk operating system",
            ("mh-db-01", "65536"),
        ),
        RetrievalProbe(
            "databases data stores SQL Oracle PostgreSQL engines",
            ("RecordsDB", "PortalDB"),
        ),
        RetrievalProbe(
            "non-functional requirements latency availability RTO RPO SLA HIPAA",
            ("RTO", "RPO", "99.95"),
        ),
    ),
)

ATLAS = EstateLabels(
    name="atlas",
    subdir="eval/atlas",
    files=(
        ("discovery-export.json", "application/json"),
        ("cmdb.xlsx", _XLSX),
        ("architecture.docx", _DOCX),
        ("questionnaire.docx", _DOCX),
    ),
    expected_servers=frozenset(
        {"atlas-web-01", "atlas-web-02", "atlas-cart-01", "atlas-db-01", "atlas-cache-01", "atlas-worker-01"}
    ),
    expected_applications=frozenset({"storefront", "cart-service", "inventory-service"}),
    expected_databases=frozenset({"cartdb", "inventorydb"}),
    sizing=(
        # Units live in the VALUES here, including TB-scale disks.
        SizingFact("atlas-web-01", vcpus=4, memory_gb=16, disk_gb=500, os="Ubuntu 22.04"),
        SizingFact("atlas-web-02", vcpus=4, memory_gb=16, disk_gb=500, os="Ubuntu 22.04"),
        SizingFact("atlas-cart-01", vcpus=8, memory_gb=32, disk_gb=1024, os="RHEL 9"),  # 1 TB
        SizingFact("atlas-db-01", vcpus=16, memory_gb=128, disk_gb=2048, os="Windows Server 2022"),  # 2 TB
        SizingFact("atlas-cache-01", vcpus=8, memory_gb=32, os="Ubuntu 22.04"),  # merged datacenter cell
    ),
    expected_nfr_attributes=frozenset({"compliance"}),
    retrieval_probes=(
        RetrievalProbe(
            "servers hostnames operating systems infrastructure inventory",
            ("atlas-cart-01", "atlas-db-01", "atlas-web-01"),
        ),
        RetrievalProbe(
            "server vCPU memory storage disk operating system",
            ("atlas-db-01", "2 TB", "128 GB"),
        ),
        RetrievalProbe(
            "databases data stores SQL Oracle PostgreSQL engines",
            ("CartDB", "InventoryDB"),
        ),
    ),
)

ORION = EstateLabels(
    name="orion",
    subdir="eval/orion",
    files=(
        ("server-inventory.csv", "text/csv"),
        ("architecture.docx", _DOCX),
        ("requirements.docx", _DOCX),
    ),
    expected_servers=frozenset(
        {
            "orion-web-01",
            "orion-web-02",
            "orion-app-01",
            "orion-db-01",
            "orion-legacy-01",
            "orion-legacy-02",
            "orion-cache-01",
            "orion-mq-01",
        }
    ),
    expected_applications=frozenset({"trading-gateway", "risk-engine", "settlement-service"}),
    expected_databases=frozenset({"riskdb", "tradesdb"}),
    sizing=(
        SizingFact("orion-web-01", vcpus=4, memory_gb=16, disk_gb=200, os="RHEL 9"),
        SizingFact("orion-web-02", vcpus=4, memory_gb=16, disk_gb=200, os="RHEL 9"),
        # OS conflict: CMDB (precedence 100) says 2022, architecture (80) says 2019 —
        # reconciliation must keep the CMDB's value.
        SizingFact("orion-app-01", vcpus=8, memory_gb=32, disk_gb=500, os="Windows Server 2022"),
        SizingFact("orion-db-01", vcpus=16, memory_gb=128, disk_gb=2000, os="Oracle Linux 8"),
        SizingFact("orion-legacy-01", vcpus=8, memory_gb=32, disk_gb=500, os="AIX 7.2"),  # unsupported
        SizingFact("orion-legacy-02", vcpus=4, memory_gb=16, disk_gb=200, os="Solaris 11"),  # unsupported
        SizingFact("orion-cache-01", vcpus=4, memory_gb=16, disk_gb=100, os="Ubuntu 22.04"),
        SizingFact("orion-mq-01", vcpus=4, memory_gb=16, disk_gb=100, os="Debian 12"),
    ),
    expected_nfr_attributes=frozenset(
        {"compliance", "rto", "rpo", "availability", "latency", "encryption", "data_residency", "scalability"}
    ),
    retrieval_probes=(
        RetrievalProbe(
            "servers hostnames operating systems infrastructure inventory",
            ("orion-web-01", "orion-app-01", "orion-legacy-01"),
        ),
        RetrievalProbe(
            "server vCPU memory storage disk operating system",
            ("orion-db-01", "128"),
        ),
        RetrievalProbe(
            "databases data stores SQL Oracle PostgreSQL engines",
            ("RiskDB", "TradesDB"),
        ),
        RetrievalProbe(
            "non-functional requirements latency availability RTO RPO SLA PCI SOX",
            ("RTO", "RPO", "99.99"),
        ),
    ),
)

ALL_ESTATES = {e.name: e for e in (CONTOSO, MERIDIAN, ATLAS, ORION)}
