# Stress estate — Zephyr Logistics

A realistic, deliberately messy discovery pack for one fictional company. Every document is
a view of the **same** estate — 27 named hosts (2 of them retired) across two datacenters, a 60-VM telematics pool
and a small AWS footprint — produced by a different tool or team at a different time, with
the distortions those sources really have: stale CMDB records, MB units, locale-specific
CSVs, physical boxes invisible to vCenter, undocumented dependencies, conflicting RTOs,
formula-only totals, non-inventory tables, leaked credentials and a prompt-injection line.

**What is actually true, and every trap, is in [`ANSWER_KEY.md`](ANSWER_KEY.md)**
(machine-readable: `answer-key.json`). Score the pipeline against it with:

```bash
python scripts/score_stress_estate.py          # offline heuristic path
python scripts/score_stress_estate.py --real   # configured LLM, e.g. Azure OpenAI
```

| File | Format | Real-world source and what it stresses |
|------|--------|----------------------------------------|
| `architecture-brief.pdf` | PDF | Architecture brief (2025-10): prose + ruled table; stale vCPU |
| `capacity-report.pdf` | PDF | Two-column capacity report (reading-order reflow) |
| `rack-layout.pdf` | PDF | Borderless rack sheet; the only view of physical boxes |
| `scanned-runbook.pdf` | PDF | Image-only scan → empty-extraction gap / OCR |
| `protected-capacity-plan.pdf` | PDF | Password-protected → unreadable-document gap |
| `cmdb-export.xlsx` | XLSX | Stale CMDB: merged cells, retired hosts, formula totals, hidden sheet, repeated header |
| `rvtools-export.xlsx` | XLSX | vCenter export: MB units, display-name drift, templates, ESXi hosts |
| `integration-register.xlsx` | XLSX | Interface register — tabular but not a server list |
| `cost-baseline.xlsx` | XLSX | EUR run-cost baseline with formulas — numeric but not a server list |
| `fleet-inventory.csv` | CSV | 60-VM worker pool; blanks and a duplicate row |
| `messy-inventory.csv` | CSV | Hand-kept ops sheet: ragged rows, placeholders |
| `perf-metrics.csv` | CSV | Monitoring P95s, ';' + decimal commas, partial coverage |
| `dependency-connections.csv` | CSV | Azure Migrate dependency export: undocumented edge, shadow IT, noise |
| `license-inventory.csv` | CSV | Licences & support dates (end-of-support blockers) |
| `cloud-assets.json` | JSON | 2025 discovery snapshot + AWS, nested record key |
| `migration-runbook.md` | Markdown | Failover runbook with a pipe table |
| `workshop-notes-2026-09-10.md` | Markdown | Freshest facts, hedged; decisions and actions |
| `multilingual-notes.md` | Markdown | CJK / Devanagari / Arabic sentence splitting |
| `email-payments-timing.txt` | Text | Forwarded thread: constraints amid quoting, signatures, PII |
| `vendor-handover.txt` | Text | MSP handover with a prompt-injection line |
| `empty-notes.txt` | Text | Whitespace only → empty-extraction gap |
| `solution-architecture.docx` | DOCX | Target architecture with an embedded table |
| `requirements-nfr.docx` | DOCX | SLA / RTO / RPO / PCI (2026-05) |
| `dr-plan.docx` | DOCX | DR plan (2023): RTO disagrees with the NFR pack; failed test |
| `discovery-questionnaire.docx` | DOCX | Q/A pairs (misses the scan-found dependency) |
| `app-portfolio.zip` | ZIP | Node/Python/.NET/Java + node_modules, .git, committed secrets |
| `legacy-spec.doc` | OLE2 | Legacy binary .doc → rejection |

Regenerate (deterministic) with:

```bash
python scripts/generate_stress_fixtures.py
```

Requires `reportlab`, `Pillow`, `pypdf`, `openpyxl` and `python-docx` (dev/test dependencies).
