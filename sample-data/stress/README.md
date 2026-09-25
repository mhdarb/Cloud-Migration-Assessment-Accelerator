# Stress fixtures — Zephyr Logistics estate

A deliberately-messy, format-diverse corpus for exercising ingestion/chunking
robustness (distinct from the clean Contoso golden set in `sample-data/`).

| File | Format | Type | Robustness path exercised |
|------|--------|------|---------------------------|
| `architecture-brief.pdf` | PDF | architecture | prose + ruled table → `<<TABLE>>` block |
| `capacity-report.pdf` | PDF | architecture | two-column reading-order reflow |
| `rack-layout.pdf` | PDF | inventory | borderless (whitespace-aligned) table recovery |
| `scanned-runbook.pdf` | PDF | runbook | image-only → empty-extraction gap / OCR |
| `protected-capacity-plan.pdf` | PDF | — | password-protected → unreadable-document gap |
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
