#!/usr/bin/env python3
"""Generate a varied, deliberately-messy "production-like" assessment estate.

Where `generate_sample_data.py` produces the clean Contoso golden set, this script
produces a *stress* corpus for a second fictional company, Zephyr Logistics, whose
documents each exercise a specific parsing/chunking robustness path:

    architecture-brief.pdf         PDF prose + a ruled (bordered) table
    capacity-report.pdf            two-column PDF (reading-order reflow)
    rack-layout.pdf                borderless / whitespace-aligned PDF table
    scanned-runbook.pdf            image-only PDF (empty-extraction gap / OCR)
    cmdb-export.xlsx               multi-sheet: title band, merged DATA cells, hidden
                                   second header row (shape guard), sizing summary
    fleet-inventory.csv            large flat CMDB CSV (row grouping + summary)
    messy-inventory.csv            genuinely ragged columns (shape-guard fallback gap)
    cloud-assets.json              records under a non-"servers" key, nested
    migration-runbook.md           runbook prose + a markdown pipe table
    multilingual-notes.md          CJK + Devanagari + Arabic (sentence splitting)
    solution-architecture.docx     prose with an embedded table (docx table walk)
    requirements-nfr.docx          SLA / RTO / RPO / PCI requirements pack
    discovery-questionnaire.docx   Q/A questionnaire pairs
    app-portfolio.zip              Node + Python + .NET + Java manifests
    empty-notes.txt                near-empty text (empty-extraction gap)
    legacy-spec.doc                OLE2 magic bytes (legacy .doc rejection)

Regenerate with:  python scripts/generate_stress_fixtures.py
"""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "sample-data" / "stress"


# --------------------------------------------------------------------------- #
# PDFs (reportlab)
# --------------------------------------------------------------------------- #
def write_architecture_pdf() -> None:
    """Multi-page prose with a genuinely ruled table — exercises pdfplumber table
    extraction (→ <<TABLE>> block) alongside prose from the non-table regions."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    styles = getSampleStyleSheet()
    story = [
        Paragraph("Zephyr Logistics — Platform Architecture Brief", styles["Title"]),
        Paragraph(
            "This brief captures the current-state architecture of Zephyr's order "
            "management and fleet-tracking platforms ahead of the Azure migration "
            "assessment. The estate spans three tiers hosted across two on-premise "
            "datacenters (DC-North and DC-South) with a small AWS footprint for "
            "analytics.",
            styles["BodyText"],
        ),
        Paragraph(
            "The Order API is business critical and depends on the Payments Service "
            "and the Identity Provider. Fleet Tracker ingests GPS telemetry via a "
            "Kafka topic and writes to a PostgreSQL time-series store.",
            styles["BodyText"],
        ),
        Spacer(1, 12),
        Paragraph("Core server inventory (extract):", styles["Heading2"]),
        Table(
            [
                ["hostname", "role", "os", "vcpu", "memory_gb"],
                ["ord-api-01", "Order API", "RHEL 9", "8", "32"],
                ["ord-api-02", "Order API", "RHEL 9", "8", "32"],
                ["pay-svc-01", "Payments", "Windows Server 2022", "4", "16"],
                ["fleet-db-01", "PostgreSQL", "Ubuntu 22.04", "16", "64"],
            ],
            style=TableStyle(
                [
                    ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                    ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
                    ("FONTSIZE", (0, 0), (-1, -1), 9),
                ]
            ),
        ),
        Spacer(1, 12),
        Paragraph(
            "Note: pay-svc-01 was documented as Windows Server 2016 in the 2023 "
            "runbook; operations confirm it was rebuilt on 2022 during the last "
            "patch cycle. This conflict is intentional for reconciliation testing.",
            styles["BodyText"],
        ),
    ]
    SimpleDocTemplate(str(OUT / "architecture-brief.pdf"), pagesize=letter).build(story)


def write_two_column_pdf() -> None:
    """Two genuine text columns — exercises multi-column reading-order reflow (without
    it, extract_text interleaves the columns scan-line by scan-line)."""
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import BaseDocTemplate, Frame, FrameBreak, PageTemplate, Paragraph

    styles = getSampleStyleSheet()
    width, height = letter
    gutter = 24
    col_w = (width - 72 - gutter) / 2
    left = Frame(36, 36, col_w, height - 72, id="left")
    right = Frame(36 + col_w + gutter, 36, col_w, height - 72, id="right")
    doc = BaseDocTemplate(str(OUT / "capacity-report.pdf"), pagesize=letter)
    doc.addPageTemplates([PageTemplate(id="two", frames=[left, right])])

    left_para = (
        "Capacity Report — Order Management. Peak throughput during the month-end "
        "close reaches 4,200 requests per second against the Order API. The current "
        "two-node cluster sustains this at 70 percent CPU with headroom for a single "
        "node failure. Memory pressure is the binding constraint during batch "
        "reconciliation, when the working set grows to roughly 26 GB per node."
    )
    right_para = (
        "Capacity Report — Fleet Tracking. Telemetry ingestion averages 18,000 GPS "
        "events per second with bursts to 45,000 during shift changes. The Kafka "
        "cluster is provisioned for three brokers; consumer lag stays under two "
        "seconds. The PostgreSQL time-series store grows about 40 GB per week and is "
        "the leading candidate for a managed-database target in the migration."
    )
    doc.build(
        [
            Paragraph("Left column — Order Management", styles["Heading2"]),
            Paragraph(left_para, styles["BodyText"]),
            FrameBreak(),
            Paragraph("Right column — Fleet Tracking", styles["Heading2"]),
            Paragraph(right_para, styles["BodyText"]),
        ]
    )


def write_borderless_pdf() -> None:
    """A whitespace-aligned table with NO ruling lines — exercises the borderless-table
    text-alignment retry (`_find_tables_robust`)."""
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas

    c = canvas.Canvas(str(OUT / "rack-layout.pdf"), pagesize=letter)
    _, height = letter
    c.setFont("Helvetica-Bold", 12)
    c.drawString(72, height - 72, "DC-North Rack Layout (no gridlines)")
    c.setFont("Courier", 10)
    cols_x = [72, 210, 320, 430]
    rows = [
        ["rack", "hostname", "u_height", "power_w"],
        ["R01", "ord-api-01", "2", "450"],
        ["R01", "ord-api-02", "2", "450"],
        ["R02", "pay-svc-01", "1", "300"],
        ["R02", "fleet-db-01", "4", "820"],
        ["R03", "edge-lb-01", "1", "180"],
    ]
    y = height - 108
    for row in rows:
        for x, cell in zip(cols_x, row):
            c.drawString(x, y, cell)
        y -= 20
    c.showPage()
    c.save()


def write_scanned_pdf() -> None:
    """An image-only page with NO text layer — exercises the empty-extraction gap (and
    the OCR path when OCR_ENABLED + tesseract are present)."""
    from PIL import Image, ImageDraw
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas

    img = Image.new("RGB", (1000, 700), "white")
    draw = ImageDraw.Draw(img)
    draw.rectangle([40, 40, 960, 660], outline="black", width=3)
    draw.text((80, 90), "ZEPHYR NETWORK RUNBOOK (scanned)", fill="black")
    draw.text((80, 140), "Step 1: failover edge-lb-01 -> edge-lb-02", fill="black")
    draw.text((80, 180), "Step 2: drain Order API node, patch, rejoin", fill="black")
    draw.rectangle([80, 240, 400, 420], outline="blue", width=2)
    draw.text((110, 320), "DC-North", fill="blue")

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    c = canvas.Canvas(str(OUT / "scanned-runbook.pdf"), pagesize=letter)
    width, height = letter
    c.drawImage(ImageReader(buf), 36, 120, width=width - 72, height=height - 240)
    c.showPage()
    c.save()


# --------------------------------------------------------------------------- #
# Spreadsheets
# --------------------------------------------------------------------------- #
def write_cmdb_xlsx() -> None:
    """Multi-sheet workbook that stresses header/merge/shape handling:
      - Servers: a title band + blank row above the real header, a 'Datacenter'
        column merged down across rows (merged DATA cells), sizing columns for the
        computed summary.
      - Network: a hidden second header row among the data (shape guard).
    """
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "Servers"
    ws.append(["Zephyr Logistics — CMDB Export (Q3)"])  # title band
    ws.append([])  # blank separator row
    ws.append(["hostname", "datacenter", "os", "vcpu", "memory_gb", "disk_gb", "cpu_utilization_pct"])
    ws.append(["ord-api-01", "DC-North", "RHEL 9", 8, 32, 200, 71])
    ws.append(["ord-api-02", None, "RHEL 9", 8, 32, 200, 68])
    ws.append(["pay-svc-01", None, "Windows Server 2022", 4, 16, 120, 55])
    ws.append(["fleet-db-01", "DC-South", "Ubuntu 22.04", 16, 64, 1024, 62])
    ws.append(["fleet-db-02", None, "Ubuntu 22.04", 16, 64, 1024, 60])
    ws.merge_cells("B4:B6")  # DC-North merged across the three DC-North rows
    ws.merge_cells("B7:B8")  # DC-South merged across the two DC-South rows

    net = wb.create_sheet("Network")
    net.append(["device", "mgmt_ip", "vlan"])
    net.append(["Device", "Management IP", "VLAN"])  # hidden SECOND header row
    net.append(["edge-lb-01", "10.1.0.10", "20"])
    net.append(["edge-lb-02", "10.1.0.11", "20"])
    net.append(["core-sw-01", "10.1.0.2", "10"])

    wb.save(OUT / "cmdb-export.xlsx")


def write_fleet_csv() -> None:
    """A larger flat CMDB CSV (60 hosts) to exercise adaptive row grouping and the
    computed numeric summary chunk. A few cells are intentionally blank."""
    lines = ["hostname,role,os,vcpu,memory_gb,disk_gb,region"]
    roles = ["web", "app", "db", "cache", "worker"]
    oses = ["RHEL 9", "Ubuntu 22.04", "Windows Server 2022"]
    for i in range(1, 61):
        vcpu = "" if i % 17 == 0 else (2 * (1 + i % 8))
        mem = 4 * (1 + i % 8)
        disk = 100 + (i % 5) * 120
        lines.append(
            f"fleet-{i:03d},{roles[i % len(roles)]},{oses[i % len(oses)]},"
            f"{vcpu},{mem},{disk},{'eastus' if i % 2 else 'westeurope'}"
        )
    (OUT / "fleet-inventory.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_messy_csv() -> None:
    """Genuinely ragged rows (differing column counts) — exercises the shape guard's
    fallback-to-prose + surfaced gap, rather than silent mis-row-grouping."""
    rows = [
        "hostname,os,vcpu,notes",
        "srv-a,RHEL 9,4,ok",
        "srv-b,Ubuntu 22.04",  # missing columns
        "srv-c,Windows,8,decommission planned,extra-trailing-field",  # too many
        "srv-d,RHEL 9,4,ok",
    ]
    (OUT / "messy-inventory.csv").write_text("\n".join(rows) + "\n", encoding="utf-8")


def write_assets_json() -> None:
    """Inventory records under a non-'servers' key, nested one level — exercises the
    generalized record discovery (`_find_record_list`)."""
    payload = {
        "generatedBy": "cloud-discovery-tool",
        "inventory": {
            "assets": [
                {"asset_id": "vm-101", "name": "ord-api-01", "vcpu": 8, "memory_gb": 32, "os": "RHEL 9"},
                {"asset_id": "vm-102", "name": "pay-svc-01", "vcpu": 4, "memory_gb": 16, "os": "Windows Server 2022"},
                {"asset_id": "vm-103", "name": "fleet-db-01", "vcpu": 16, "memory_gb": 64, "os": "Ubuntu 22.04"},
            ]
        },
    }
    (OUT / "cloud-assets.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")


# --------------------------------------------------------------------------- #
# Markdown / text
# --------------------------------------------------------------------------- #
def write_runbook_md() -> None:
    """Runbook prose + a GitHub-style pipe table — exercises markdown-table → <<TABLE>>
    conversion for a plain-text upload."""
    md = """# Zephyr Order API — Failover Runbook

This runbook covers the manual failover procedure for the Order API tier.

## Preconditions

- On-call engineer has access to DC-North and DC-South jump hosts.
- Payments Service health check is green.

## Node roles

| hostname | role | datacenter | priority |
| --- | --- | --- | --- |
| ord-api-01 | primary | DC-North | 1 |
| ord-api-02 | primary | DC-North | 1 |
| ord-api-03 | standby | DC-South | 2 |

## Procedure

1. Announce the maintenance window in the operations channel.
2. Drain ord-api-01, verify connections migrate to ord-api-02.
3. Promote the DC-South standby only if both DC-North nodes are unhealthy.
"""
    (OUT / "migration-runbook.md").write_text(md, encoding="utf-8")


def write_multilingual_md() -> None:
    """Short non-Latin passages — exercises sentence splitting beyond Western/CJK."""
    md = """# Migration Notes (multilingual)

系统当前部署在两个数据中心。订单服务是关键业务。需要迁移到云。

माइग्रेशन की प्राथमिकता उच्च है। डेटाबेस को पहले स्थानांतरित करें।

خطة الترحيل جاهزة؟ نعم، تبدأ في الربع الثاني.
"""
    (OUT / "multilingual-notes.md").write_text(md, encoding="utf-8")


def write_empty_txt() -> None:
    """Near-empty text file — exercises the empty-extraction gap for a non-PDF format."""
    (OUT / "empty-notes.txt").write_text("  \n", encoding="utf-8")


def write_legacy_doc() -> None:
    """Real OLE2 magic bytes with junk body — exercises the legacy binary .doc rejection
    (clear 're-save as .docx' error), not python-docx's opaque failure."""
    ole2 = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
    (OUT / "legacy-spec.doc").write_bytes(ole2 + b"\x00" * 512)


# --------------------------------------------------------------------------- #
# DOCX
# --------------------------------------------------------------------------- #
def write_solution_architecture_docx() -> None:
    """Architecture prose with a table embedded between paragraphs — exercises the
    document-order walk and embedded-table chunking."""
    from docx import Document

    doc = Document()
    doc.add_heading("Zephyr Logistics — Target Solution Architecture", 0)
    doc.add_paragraph(
        "The target architecture moves the Order API and Payments Service to Azure "
        "App Service, with the fleet time-series store on Azure Database for "
        "PostgreSQL Flexible Server. Kafka is replaced by Azure Event Hubs."
    )
    doc.add_paragraph("The proposed target SKUs per component are summarized below.")
    table = doc.add_table(rows=1, cols=4)
    table.style = "Light Grid Accent 1"
    hdr = table.rows[0].cells
    hdr[0].text, hdr[1].text, hdr[2].text, hdr[3].text = "component", "current", "target", "notes"
    for comp, cur, tgt, note in [
        ("Order API", "2x RHEL VM", "App Service P2v3", "autoscale 2-6"),
        ("Payments", "1x Windows VM", "App Service P1v3", "PCI scope"),
        ("Fleet DB", "PostgreSQL on VM", "Flexible Server GP_D8ds", "HA zone-redundant"),
    ]:
        row = table.add_row().cells
        row[0].text, row[1].text, row[2].text, row[3].text = comp, cur, tgt, note
    doc.add_paragraph(
        "Migration sequencing: the Fleet DB moves first (lowest coupling), followed "
        "by the Order API, with Payments last due to PCI re-certification."
    )
    doc.save(OUT / "solution-architecture.docx")


def write_requirements_docx() -> None:
    from docx import Document

    doc = Document()
    doc.add_heading("Zephyr Wave-1 Requirements & NFR Pack", 0)
    doc.add_paragraph("Business requirements document (BRD) for cloud migration readiness.")
    doc.add_heading("Non-functional requirements", level=1)
    for line in [
        "SLA: 99.95% monthly availability for the Order API.",
        "RTO: 2 hours for the Order API; RPO: 5 minutes for the Payments datastore.",
        "Latency p95 must stay under 200 ms for order submission.",
        "Must sustain 5,000 concurrent users at month-end peak.",
        "Data residency: EU order data must remain in West Europe.",
    ]:
        doc.add_paragraph(line)
    doc.add_heading("Compliance", level=1)
    doc.add_paragraph("PCI-DSS controls apply to the Payments Service and its datastore.")
    doc.add_paragraph("Encryption: TLS 1.2+ in transit, AES-256 at rest.")
    doc.save(OUT / "requirements-nfr.docx")


def write_questionnaire_docx() -> None:
    from docx import Document

    doc = Document()
    doc.add_heading("Discovery Questionnaire", 0)
    doc.add_paragraph("Business & technical Q&A for Zephyr wave-1 candidates.")
    qa = [
        ("Q1: Which applications are business critical?",
         "The Order API and Payments Service are business critical; Fleet Tracker is medium."),
        ("Q2: Are there compliance constraints?",
         "Payments data is PCI-relevant and EU order data must remain in West Europe."),
        ("Q3: What are the known dependencies?",
         "The Order API depends on Payments and the Identity Provider; Fleet Tracker reads Kafka."),
        ("Q4: What is not documented?",
         "Firewall rules between DC-North and DC-South are not captured in this pack."),
    ]
    for q, a in qa:
        doc.add_paragraph(q)
        doc.add_paragraph(a)
    doc.save(OUT / "discovery-questionnaire.docx")


# --------------------------------------------------------------------------- #
# Code snapshot (ZIP) — multiple runtimes
# --------------------------------------------------------------------------- #
def write_portfolio_zip() -> None:
    """A multi-app portfolio exercising Node, Python, .NET and Java manifest parsers."""
    files: dict[str, str] = {
        # Node order API
        "order-api/package.json": json.dumps(
            {
                "name": "zephyr-order-api",
                "version": "2.3.1",
                "engines": {"node": ">=20"},
                "dependencies": {"express": "^4.19.2", "pg": "^8.12.0", "kafkajs": "^2.2.4"},
            },
            indent=2,
        ),
        "order-api/Dockerfile": (
            "FROM node:20-alpine\nWORKDIR /app\nCOPY package.json ./\n"
            "RUN npm ci --omit=dev\nCOPY . .\nEXPOSE 8080\nCMD [\"node\",\"server.js\"]\n"
        ),
        "order-api/docker-compose.yml": (
            "services:\n  api:\n    build: .\n    ports: [\"8080:8080\"]\n"
            "  postgres:\n    image: postgres:16\n  kafka:\n    image: bitnami/kafka:3.7\n"
        ),
        # Python fleet ingestor
        "fleet-ingestor/requirements.txt": "fastapi==0.115.0\nconfluent-kafka==2.5.0\npsycopg[binary]==3.2.1\n",
        "fleet-ingestor/pyproject.toml": (
            "[project]\nname = \"fleet-ingestor\"\nversion = \"0.4.0\"\n"
            "requires-python = \">=3.11\"\n"
        ),
        "fleet-ingestor/Dockerfile": (
            "FROM python:3.11-slim\nWORKDIR /app\nCOPY . .\nRUN pip install -r requirements.txt\n"
        ),
        # .NET payments service
        "payments/Payments.csproj": (
            "<Project Sdk=\"Microsoft.NET.Sdk.Web\">\n  <PropertyGroup>\n"
            "    <TargetFramework>net8.0</TargetFramework>\n  </PropertyGroup>\n"
            "  <ItemGroup>\n    <PackageReference Include=\"Npgsql\" Version=\"8.0.3\" />\n"
            "  </ItemGroup>\n</Project>\n"
        ),
        "payments/appsettings.json": json.dumps(
            {"ConnectionStrings": {"Payments": "Host=pay-db;Database=payments"}}, indent=2
        ),
        # Java identity provider
        "identity/pom.xml": (
            "<project>\n  <modelVersion>4.0.0</modelVersion>\n"
            "  <groupId>com.zephyr</groupId>\n  <artifactId>identity</artifactId>\n"
            "  <version>1.1.0</version>\n  <dependencies>\n    <dependency>\n"
            "      <groupId>org.springframework.boot</groupId>\n"
            "      <artifactId>spring-boot-starter-web</artifactId>\n"
            "    </dependency>\n  </dependencies>\n</project>\n"
        ),
    }
    zip_path = OUT / "app-portfolio.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for arcname, content in files.items():
            zf.writestr(arcname, content)


# --------------------------------------------------------------------------- #
def write_readme() -> None:
    (OUT / "README.md").write_text(
        """# Stress fixtures — Zephyr Logistics estate

A deliberately-messy, format-diverse corpus for exercising ingestion/chunking
robustness (distinct from the clean Contoso golden set in `sample-data/`).

| File | Format | Type | Robustness path exercised |
|------|--------|------|---------------------------|
| `architecture-brief.pdf` | PDF | architecture | prose + ruled table → `<<TABLE>>` block |
| `capacity-report.pdf` | PDF | architecture | two-column reading-order reflow |
| `rack-layout.pdf` | PDF | inventory | borderless (whitespace-aligned) table recovery |
| `scanned-runbook.pdf` | PDF | runbook | image-only → empty-extraction gap / OCR |
| `cmdb-export.xlsx` | XLSX | inventory | title band, merged DATA cells, hidden 2nd header (shape guard), summary |
| `fleet-inventory.csv` | CSV | inventory | 60 rows → adaptive row grouping + computed summary |
| `messy-inventory.csv` | CSV | inventory | ragged columns → shape-guard fallback gap |
| `cloud-assets.json` | JSON | inventory | records under a non-`servers`, nested key |
| `migration-runbook.md` | Markdown | runbook | markdown pipe table → `<<TABLE>>` block |
| `multilingual-notes.md` | Markdown | unknown | CJK / Devanagari / Arabic sentence splitting |
| `solution-architecture.docx` | DOCX | architecture | embedded table in prose (document-order walk) |
| `requirements-nfr.docx` | DOCX | requirements | SLA / RTO / RPO / PCI |
| `discovery-questionnaire.docx` | DOCX | questionnaire | Q/A pair chunking |
| `app-portfolio.zip` | ZIP | code_snapshot | Node + Python + .NET + Java manifests |
| `empty-notes.txt` | text | unknown | near-empty → empty-extraction gap |
| `legacy-spec.doc` | OLE2 | — | legacy binary `.doc` rejection |

Regenerate with:

```bash
python scripts/generate_stress_fixtures.py
```

Requires `reportlab` and `Pillow` (dev/test dependencies).
""",
        encoding="utf-8",
    )


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    generators = [
        write_architecture_pdf,
        write_two_column_pdf,
        write_borderless_pdf,
        write_scanned_pdf,
        write_cmdb_xlsx,
        write_fleet_csv,
        write_messy_csv,
        write_assets_json,
        write_runbook_md,
        write_multilingual_md,
        write_empty_txt,
        write_legacy_doc,
        write_solution_architecture_docx,
        write_requirements_docx,
        write_questionnaire_docx,
        write_portfolio_zip,
        write_readme,
    ]
    for gen in generators:
        gen()
    files = sorted(p.name for p in OUT.iterdir() if p.is_file())
    print(f"Wrote {len(files)} fixtures to {OUT}:")
    for name in files:
        print(f"  - {name}")


if __name__ == "__main__":
    main()
