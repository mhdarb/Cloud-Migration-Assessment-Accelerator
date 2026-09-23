# Azure AI Landing Zone alignment

This accelerator is an **application landing zone (spoke) workload** candidate. It consumes shared Azure AI, search, identity, ingress, policy, and observability capabilities while keeping workload data and application services in the spoke.

## Hub vs spoke

```text
PLATFORM / AI HUB (enterprise-owned)
  App Gateway · Firewall · API Management
  Entra ID · Defender · Azure Policy · Monitor · FinOps
  Shared: Azure AI Foundry / OpenAI · Azure AI Search · (optional Content Safety)

APPLICATION SPOKE (this accelerator)
  API (Container Apps / App Service) · Web
  Postgres · Storage (uploads) · Key Vault refs
  Private endpoints to hub AI + data services
```

| Concern | Owner | This repo |
|---------|--------|-----------|
| Ingress, WAF, APIM JWT | Hub | Documented; `APIM_BASE_URL` optional |
| Shared models / search | Hub (or shared sub) | `AZURE_OPENAI_*`, `AZURE_SEARCH_*` + MI |
| Workload app + data | Spoke | `apps/api`, `apps/web`, `infra/` |
| Policy / Defender | Hub + spoke tags | Spoke Bicep diagnostics + tags |
| Assessment workflow | Workload | Review gate (`ENFORCE_REVIEW`) and follow-up log in API |

## Profiles

| `APP_PROFILE` | Intent |
|---------------|--------|
| `local` (default) | Laptop demo: SQLite, mock LLM, local embeddings + FAISS. Optional Azure OpenAI when `MOCK_LLM=false`. |
| `lz` | Landing Zone spoke: Postgres required, mock/local embeddings forbidden, Azure OpenAI + AI Search required, guardrails on, `API_AUTH_KEY` or `APIM_BASE_URL` required, content-safety failures must fail closed, pipeline locking must be DB-backed (multi-worker safe). |

Validate on API startup via `Settings.validate_profile()`.

### Example `lz` env

```bash
APP_PROFILE=lz
USE_MANAGED_IDENTITY=true
MOCK_LLM=false
LOCAL_EMBEDDINGS=false
RAG_ENABLED=true
GUARDRAILS_ENABLED=true
CONTENT_SAFETY_FAIL_OPEN=false
PIPELINE_LOCK_BACKEND=db
DATABASE_URL=postgresql+psycopg://...
AZURE_OPENAI_ENDPOINT=https://<foundry-or-aoai>.openai.azure.com/
AZURE_SEARCH_ENDPOINT=https://<search>.search.windows.net
AZURE_SEARCH_INDEX=cmaa-chunks
APPLICATIONINSIGHTS_CONNECTION_STRING=...
# Required: Entra JWT at APIM, or a spoke API_AUTH_KEY for non-APIM demos
APIM_BASE_URL=https://<apim>.azure-api.net/cmaa
API_AUTH_KEY=
```

`CONTENT_SAFETY_FAIL_OPEN=false` and `PIPELINE_LOCK_BACKEND=db` are enforced by `validate_profile()` for `lz` — startup fails fast if either is left at its `local`-profile default. See [DYNAMIC_ARCHITECTURE.md](DYNAMIC_ARCHITECTURE.md) for what each controls.

## Identity

- **`USE_MANAGED_IDENTITY=true`**: `DefaultAzureCredential` for OpenAI + AI Search (`apps/api/app/azure_clients.py`).
- **Keys**: break-glass / local only; do not ship keys in LZ production.

## Ingress

Production path: **Browser → App Gateway / APIM (Entra JWT) → spoke API**.  
Spoke may set `API_AUTH_KEY` for non-APIM demos; APIM should remain the system of record for authn/z.

## Infrastructure

See [`infra/README.md`](../infra/README.md) for Bicep spoke skeleton (Container Apps, Postgres, Storage, Key Vault, Log Analytics, role assignments to shared AI).

## Platform evolution

Use normal architecture governance for threat modeling, ADRs, policy baselines, capacity, FinOps, deployment, compliance, and human sign-off. Workload claim review (Accept/Override/Reject) remains the responsible-AI control for assessment outputs.
