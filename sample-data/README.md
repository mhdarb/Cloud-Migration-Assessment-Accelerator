# Sample data — Contoso estate

Synthetic documents:

| File | Type | Purpose |
|------|------|---------|
| `architecture-overview.docx` | Architecture | Apps, servers, DBs, interfaces |
| `cmdb-inventory.xlsx` | Inventory | CMDB export with sizing metrics, one missing-data case, and an OS conflict |
| `assessment-questionnaire.docx` | Questionnaire | Criticality, compliance, gaps |
| `requirements-nfr.docx` | Requirements / NFR | SLA, RTO/RPO, PCI, residency |
| `sample-app.zip` | Code snapshot | Node billing API manifests (pg/redis/kafka) |

Regenerate with:

```bash
uv run --project apps/api python scripts/generate_sample_data.py
```
