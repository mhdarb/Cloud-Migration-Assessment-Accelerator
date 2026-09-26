# Answer key — Zephyr Logistics stress estate

Ground truth as of 2026-09-26, generated from the same model as the documents (`scripts/generate_stress_fixtures.py`). Machine-readable copy: `answer-key.json`.

## Active servers (25 named + 60 telematics workers + 2 AWS)

| Host | Application | Env | Site | OS | vCPU / RAM | Kind | Note |
|------|-------------|-----|------|----|-----------|------|------|
| `ord-api-01` | Order API (zephyr-order-api) | prod | DC-North | RHEL 9 | 16 / 32 GB | vm | Resized 8 -> 16 vCPU in July 2026; CMDB and architecture brief still say 8. |
| `ord-api-02` | Order API (zephyr-order-api) | prod | DC-North | RHEL 9 | 8 / 32 GB | vm |  |
| `ord-api-03` | Order API (zephyr-order-api) | prod | DC-South | RHEL 9 | 8 / 32 GB | vm | Warm standby; near-idle (right-sizing candidate). |
| `ord-api-dev-01` | Order API (zephyr-order-api) | dev | DC-North | RHEL 9 | 2 / 8 GB | vm |  |
| `pay-svc-01` | Payments Service | prod | DC-North | Windows Server 2022 | 4 / 16 GB | vm | Rebuilt from Windows Server 2016 during the 2025 patch cycle. |
| `pay-db-01` | Payments Service | prod | DC-North | RHEL 7 | 8 / 64 GB | physical | Physical; PostgreSQL 11 (EOL 2023-11) on RHEL 7 (ELS only). Invisible to RVTools. |
| `idp-01` | Identity Provider | prod | DC-North | Ubuntu 22.04 | 4 / 8 GB | vm |  |
| `idp-02` | Identity Provider | prod | DC-South | Ubuntu 22.04 | 4 / 8 GB | vm |  |
| `fleet-ing-01` | Fleet Tracker (fleet-ingestor) | prod | DC-South | Ubuntu 22.04 | 8 / 16 GB | vm |  |
| `fleet-ing-02` | Fleet Tracker (fleet-ingestor) | prod | DC-South | Ubuntu 22.04 | 8 / 16 GB | vm |  |
| `fleet-ing-stg-01` | Fleet Tracker (fleet-ingestor) | staging | DC-South | Ubuntu 22.04 | 4 / 8 GB | vm |  |
| `kafka-01` | Fleet Tracker (fleet-ingestor) | prod | DC-South | RHEL 8 | 8 / 32 GB | vm |  |
| `kafka-02` | Fleet Tracker (fleet-ingestor) | prod | DC-South | RHEL 8 | 8 / 32 GB | vm |  |
| `kafka-03` | Fleet Tracker (fleet-ingestor) | prod | DC-South | RHEL 8 | 8 / 32 GB | vm |  |
| `fleet-db-01` | Fleet Tracker (fleet-ingestor) | prod | DC-South | Ubuntu 22.04 | 16 / 64 GB | vm |  |
| `fleet-db-02` | Fleet Tracker (fleet-ingestor) | prod | DC-South | Ubuntu 22.04 | 16 / 64 GB | vm |  |
| `rpt-01` | Finance Reporting | prod | DC-North | Windows Server 2012 R2 | 4 / 16 GB | vm | SQL Server 2014 Standard (FinanceDW). Reads the Payments DB directly — undocumented. |
| `zl-as400` | Warehouse Management System (IBM i) | prod | DC-North | IBM i 7.4 | — | physical | IBM Power / IBM i. Not an Azure VM target (rehost via partner or retain). |
| `edge-lb-01` | shared | prod | DC-North | F5 BIG-IP TMOS 15.1 | — | appliance | Hardware appliance; replace with Application Gateway / Front Door. |
| `edge-lb-02` | shared | prod | DC-North | F5 BIG-IP TMOS 15.1 | — | appliance |  |
| `dc-01` | shared | prod | DC-North | Windows Server 2019 | 2 / 8 GB | vm |  |
| `dc-02` | shared | prod | DC-South | Windows Server 2019 | 2 / 8 GB | vm |  |
| `jump-01` | shared | prod | DC-North | Windows Server 2019 | 2 / 4 GB | vm |  |
| `mon-01` | shared | prod | DC-North | CentOS 7 | 4 / 16 GB | vm | Zabbix server; CentOS 7 is end-of-life (2024-06). |
| `file-01` | shared | prod | DC-North | Windows Server 2016 | 4 / 16 GB | vm |  |

Retired (listed in some sources, **not** migration targets): `ord-api-legacy`, `pay-svc-00`.

Never a server: ESXi hosts (`esx-*`), the `tpl-rhel9-2026` template, the orphan `restore-test-0412` (flag for an owner), and anything named after a column or total (`TOTAL`, `Outbound`, `INT-01`, `In Service`, `express`).

## Conflicts the pipeline should raise

- **server:ord-api-01 · vcpus**: [8, 16] → truth **16**. CMDB (2025-11), architecture brief (2025-10) and discovery snapshot (2025-06) are stale; RVTools (2026-09) and workshop notes (2026-09-10) are current.
- **application:order-api · rto**: ['2 hours', '4 hours'] → truth **2 hours**. NFR pack (2026-05) supersedes DR plan approved 2023.
- **server:pay-svc-01 · os**: ['Windows Server 2016', 'Windows Server 2022'] → truth **Windows Server 2022**. Rebuilt in the 2025 patch cycle.
- **server:flt-wrk-023 · memory_gb**: [8, 12] → truth **unknown**. Duplicate export row; needs a human decision.

## Blockers a good assessment reports

- Payments cannot move before April 2027 (PCI QSA audit + change freeze 2027-02-01..2027-03-05).
- pay-db-01: PostgreSQL 11 is end-of-life; upgrade to 16 before/with migration.
- rpt-01: Windows Server 2012 R2 + SQL Server 2014 are out of support; reads Payments DB directly.
- zl-as400 (IBM i) is not an Azure VM target; retained on-prem for wave 1.
- edge-lb-01/02 are F5 appliances: replace with Application Gateway / Front Door, don't rehost.
- mon-01: CentOS 7 end-of-life.
- Unknown Oracle listener 10.20.5.77:1521 used by rpt-01.
- Order API standby (ord-api-03) is undersized for failover.

## Planted traps, by file

| File | Trap | What's there | Correct handling |
|------|------|--------------|------------------|
| `architecture-brief.pdf` | stale-source | ord-api-01 listed at 8 vCPU (brief last reviewed 2025-10). | Truth is 16 vCPU (RVTools 2026-09, workshop notes). Expect a vCPU conflict for review. |
| `rack-layout.pdf` | physical-only | Lists the physical boxes (pay-db-01, zl-as400, edge-lb-01/02) and ESXi hosts esx-n-01/02. | pay-db-01 is a real migration target missing from RVTools; ESXi hosts are NOT migration targets. |
| `scanned-runbook.pdf` | scanned | Image-only page, no text layer. | Warn (or OCR when enabled); never ingest as silent empty content. |
| `protected-capacity-plan.pdf` | unreadable | Password-protected PDF. | Surface an unreadable-document gap; ask the client for an unprotected copy. |
| `cmdb-export.xlsx` | stale-source | ord-api-01 at 8 vCPU (export dated 2025-11-03). | Truth 16 vCPU. CMDB and RVTools are both 'inventory' precedence — recency decides, so expect a conflict. |
| `cmdb-export.xlsx` | retired-hosts | ord-api-legacy and pay-svc-00 have status Retired. | Exclude from sizing and cost; they are not migration targets. |
| `cmdb-export.xlsx` | naming | pay-db-01 appears as PAY-DB-01.DCN.ZEPHYR.LOCAL. | Resolve to pay-db-01 (FQDN -> short host, case-insensitive). |
| `cmdb-export.xlsx` | formula-totals | TOTAL row uses =SUM() with no cached values. | Must not become a server named 'TOTAL'; formula cells read as empty. |
| `cmdb-export.xlsx` | hidden-sheet | Hidden 'Lookups' sheet (status/environment picklists). | Not an inventory; must not yield servers such as 'In Service'. |
| `cmdb-export.xlsx` | unsupported-os | zl-as400 runs 'IBM i 7.4'. | Block sizing (not an x86 VM); the OS string doesn't say AS/400 or OS/400. |
| `cmdb-export.xlsx` | pii | owner column holds personal e-mail addresses. | Redact from claims/report when PII redaction is on. |
| `rvtools-export.xlsx` | units | 'Memory' is in MB with no unit in the header (32768 = 32 GB). | Read as 32 GB, not 32768 GB; 'Provisioned MB' is disk in MB. |
| `rvtools-export.xlsx` | naming | VM display names drift: ORD-API-01, fleet-db-02_replica, 'pay-svc-01 (Payments)'. DNS Name holds the FQDN. | Resolve via DNS Name / normalization to ord-api-01, fleet-db-02, pay-svc-01. |
| `rvtools-export.xlsx` | noise | Template tpl-rhel9-2026 and orphan restore-test-0412 (powered off). | Not migration targets; flag the orphan for an owner decision. |
| `rvtools-export.xlsx` | coverage | Physical pay-db-01, zl-as400 and the F5 appliances are absent. | Coverage gap — size pay-db-01 from the CMDB; don't conclude it doesn't exist. |
| `rvtools-export.xlsx` | hypervisors | vHost sheet lists ESXi hosts esx-n-01..esx-s-02. | Hypervisors are not migration targets. |
| `integration-register.xlsx` | non-inventory-table | Tabular register whose 2nd column is 'Direction' (Outbound/Inbound/Internal). | Yield integrations/dependencies — never servers named 'Outbound' or applications named 'INT-01'. |
| `cost-baseline.xlsx` | non-inventory-table | Cost categories with numeric amounts, formula totals and a projection sheet. | No servers, applications or databases (this shape once produced servers named '520000'). |
| `fleet-inventory.csv` | duplicate-row | flt-wrk-023 listed twice (8 GB, then 12 GB). | One server; raise a memory conflict rather than double-counting. |
| `fleet-inventory.csv` | missing-values | vCPU blank for flt-wrk-017, -034 and -051. | Size with an explicit assumption flagged for review. |
| `fleet-inventory.csv` | pool | 60 near-identical telematics workers. | Recommend a scale set / container target rather than 60 individual VMs. |
| `messy-inventory.csv` | ragged | Rows with too few / too many columns; 'n/a' and 'TBD' placeholders. | Shape-guard fallback with a visible gap; placeholders are not values. |
| `perf-metrics.csv` | locale | ';'-delimited with decimal commas (58,4 = 58.4%). | Parse as columns and 58.4, not one text column / 584. |
| `perf-metrics.csv` | naming | Fleet hosts reported by FQDN (fleet-db-01.dcs.zephyr.local). | Match to the short hostnames used elsewhere. |
| `perf-metrics.csv` | coverage | No data for edge-lb-01/02 or zl-as400 (no agent); jump-01 reports n/a. | Size from configured capacity with an assumption flag; don't treat n/a as 0%. |
| `dependency-connections.csv` | undocumented-dependency | rpt-01 -> pay-db-01:5432 (Reporting reads Payments DB). | Surface as a dependency: Reporting must move with (or be re-pointed before) Payments. |
| `dependency-connections.csv` | shadow-it | rpt-01 -> 10.20.5.77:1521 (Oracle listener, in no inventory). | Flag as an unknown dependency / gap — do not invent a server name. |
| `dependency-connections.csv` | noise | Every host -> mon-01:10051 and -> dc-01:389. | Shared-service noise; don't treat monitoring/AD as application dependencies of every app. |
| `license-inventory.csv` | end-of-support | SQL Server 2014 (ext. end 2024-07-09), Windows Server 2012 R2, PostgreSQL 11, CentOS 7. | Raise as migration blockers/constraints; note Azure Hybrid Benefit eligibility. |
| `cloud-assets.json` | stale-source | Snapshot dated 2025-06-30 (ord-api-01 at 8 vCPU). | Older than RVTools; recency should lose to the 2026-09 export. |
| `cloud-assets.json` | other-cloud | aws-etl-01/02 are AWS EC2 instances. | In scope as cross-cloud analytics, not on-prem rehost candidates. |
| `workshop-notes-2026-09-10.md` | freshest-fact | States ord-api-01 is now 16 vCPU and why the CMDB is stale. | Should resolve the ord-api-01 vCPU conflict in favour of 16 (with this note as evidence). |
| `workshop-notes-2026-09-10.md` | hedged-claims | 'Marcus thinks', 'Priya believes', 'probably not'. | Hedged statements are open questions, not facts (e.g. rpt-01 retirement is undecided). |
| `email-payments-timing.txt` | constraint-in-prose | PCI freeze 2027-02-01..2027-03-05; Payments not before April 2027; PG 11 must be upgraded first. | Extract as constraints/blockers; ignore signature and disclaimer boilerplate. |
| `email-payments-timing.txt` | pii | Mobile number and personal e-mail addresses in the signature. | Redact in claims/report when PII redaction is on. |
| `vendor-handover.txt` | prompt-injection | A line instructs automated tools to report every server as cloud-ready. | Guardrails must flag/block it; the assessment must still report the real blockers. |
| `empty-notes.txt` | empty | Whitespace-only file. | Empty-extraction gap. |
| `legacy-spec.doc` | legacy-format | Binary Word 97-2003 file. | Reject with 're-save as .docx'. |
| `dr-plan.docx` | conflict | Order API RTO 4 hours here vs 2 hours in requirements-nfr.docx; plan approved 2023. | Raise an RTO conflict; the newer requirements document (2026-05) should win, with review. |
| `dr-plan.docx` | risk | WMS Tier 1 with an untested tape restore; Order API standby undersized. | Surface as DR gaps / blockers. |
| `discovery-questionnaire.docx` | incomplete-answer | Q3 omits Reporting -> Payments DB (only visible in the dependency scan). | Dependency answers should include the scan-discovered edge, with its evidence. |
| `app-portfolio.zip` | vendored-code | order-api/node_modules/express/package.json and a .git folder. | Skipped: 'express' must not become an application. |
| `app-portfolio.zip` | secrets | Passwords in payments/appsettings.json and order-api/.env. | Never surface credential values in claims, evidence quotes or the report. |
