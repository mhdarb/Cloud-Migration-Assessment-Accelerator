# Demo walkthrough

Stakeholder-friendly path using Contoso sample data. Assumes API + UI are running ([SETUP.md](SETUP.md)).

## Prep

1. Confirm [http://localhost:8000/health](http://localhost:8000/health) returns `"status": "ok"`.
2. Open [http://localhost:3000](http://localhost:3000).
3. Sample files in `sample-data/` (run `uv run --project apps/api python scripts/generate_sample_data.py` if missing).

## Script ( ~10 minutes )

### 1. Create assessment

1. Name: `Contoso Wave-1 Discovery`
2. Upload all of:
  - `architecture-overview.docx`
  - `cmdb-inventory.xlsx`
  - `assessment-questionnaire.docx`
  - `requirements-nfr.docx`
  - `sample-app.zip`
3. Click **Create & run assessment**. Processing starts as soon as files are uploaded.

**Talking point:** Mix of architecture, CMDB, Q&A, NFR pack, and a code ZIP — same pipeline. Default uses mock LLM (no Azure key).

### 2. Overview tab

While status moves ingesting → extracting → … → completed:

- Documents show inferred types and precedence (inventory highest, then code snapshot, etc.) — the type label itself now comes from a dynamic `DocClassifier` (`GET /health/config` shows which one is active); the precedence table it feeds is still fixed and rule-based.
- Metrics after completion should include e.g.:
  - `claim_count`, `cited_claim_pct`
  - `rag_queries`, `chunks_retrieved`, `retrieval_mode`
  - `code_snapshot_count`, `manifest_files_parsed`, `nfr_claim_count`
  - `neo4j_synced` (true only if Neo4j is up)

**Talking point:** the RAG query planner filters which of its skills actually run based on which document types were uploaded — a questionnaire-only assessment runs noticeably fewer `rag_queries` than one with the full document mix.

### 3. Assessment questions

- Show the versioned standard migration question set — this base set is intentionally fixed for comparability across assessments.
- Additional estate-specific questions may appear below the base set (tagged `"origin": "dynamic"` in the `/assessment-questions` API response), generated from signals in this specific estate such as an unsupported-OS server or an open conflict. The UI doesn't yet visually distinguish them from ad-hoc questions — call this out as a follow-up if asked, or show it via `/docs` / the raw API response.
- Point out confidence, source-linked vs evidence gap, and `template` vs `llm` answer source.
- Expand evidence traces (filename, page, quote). Missing evidence is a reviewable gap, not an invented fact.

### 4. Sizing & cost

- Show each server's Azure VM and managed-disk recommendation.
- Explain required capacity, compatibility checks, alternatives, confidence, and assumptions.
- Pricing uses the local catalog by default; `AZURE_PRICING_LIVE=true` optionally refreshes VM prices.
- The recommendation is deterministic (catalog rules). Azure OpenAI can explain it but cannot change the selected SKU.

### 5. Findings

Show:

- Applications / servers / databases from inventory + architecture
- Runtime/framework from ZIP (e.g. `contoso-billing-api` → Node / Express)
- Infra deps from manifests (Postgres, Redis, Kafka)
- NFR/business claims (SLA 99.9%, RTO/RPO, PCI, data residency)
- **Conflicts** — e.g. `app-bill-01` OS inventory vs legacy note 

### 6. Graph + blast radius

1. Open **Graph**.
2. Select an application (e.g. Billing Service or contoso-billing-api).
3. **Show blast radius** (depth 2).

**Talking point:** Migration impact neighborhood — what else moves if this app moves. Source badge `neo4j` or `postgres`.

### 7. Report

- Readiness summary (inventory + code + NFR language)
- Gaps and assumptions
- Download JSON export for downstream wave planning tools

### 8. Review queue

- Accept / Override / Reject a low-confidence or conflicting claim
- Note report/entities refresh after review (human-in-the-loop)

## What “good” looks like


| Check     | Expected                                                        |
| --------- | --------------------------------------------------------------- |
| Pipeline  | Status `completed`, no hard failure without Neo4j               |
| Citations | High `cited_claim_pct` (demo often ~100% on samples)            |
| Code ZIP  | Node runtime + express; postgres/redis/kafka entities or edges  |
| NFR doc   | Multiple business/NFR claims in findings/report                 |
| Questions | Standard answers include confidence, source references, and evidence traces |
| Sizing    | VM/disk recommendation, estimate, assumptions, and alternatives |
| Conflict  | At least one OS or criticality conflict open or resolved        |
| Graph     | Interactive nodes/edges; blast radius highlights a neighborhood |


## Optional deeper demo

- Re-run pipeline with **Run pipeline** after more documents are uploaded
- Show `/docs` OpenAPI for integration conversation
- With `MOCK_LLM=false` plus Azure OpenAI: contrast extraction quality, question prose, conflict notes, and the readiness summary (SKU pick stays catalog-based)
- With Docker Neo4j: show Browser at [http://localhost:7474](http://localhost:7474) and `neo4j_synced: true`

