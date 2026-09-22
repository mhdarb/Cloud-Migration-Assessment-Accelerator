# Infrastructure (Azure AI Landing Zone spoke)

Bicep skeleton for deploying the **Cloud Migration Assessment Accelerator** as an **application landing zone (spoke)** that consumes shared hub AI services (Azure OpenAI / AI Foundry + Azure AI Search).

Designed for an Azure AI Landing Zone hub/spoke environment.

## Layout

```text
infra/
  bicep/
    main.bicep           # spoke composition
    modules/
      logAnalytics.bicep
      keyVault.bicep
      storage.bicep
      postgres.bicep
      containerApps.bicep
      roleAssignments.bicep
  parameters/
    lz.sample.bicepparam
```

## Prerequisites

- Existing **hub / shared** Azure OpenAI (or AI Foundry) + Azure AI Search (resource IDs as parameters).
- Azure CLI + Bicep.
- Entra permissions to assign roles on shared AI resources to the spoke managed identity.

## Deploy (sample)

```bash
az group create -n rg-cmaa-spoke -l eastus

az deployment group create \
  -g rg-cmaa-spoke \
  -f infra/bicep/main.bicep \
  -p infra/parameters/lz.sample.bicepparam
```

Customize `lz.sample.bicepparam` with your hub OpenAI/Search resource IDs and image name.

## What this skeleton does / does not

| Does | Does not |
|------|----------|
| Spoke RG resources + diagnostics | Full hub (APIM, Firewall, App Gateway) |
| User-assigned MI + Cognitive Services / Search RBAC | Private endpoint wiring to hub VNet (add per enterprise hub) |
| Container Apps env placeholders | Build/push CI pipeline |
| Tags for FinOps (`app`, `landingZone`) | Azure Policy initiative definitions |

Hub ingress (APIM + Entra JWT) remains enterprise-owned; point APIM backend to the Container Apps ingress FQDN after deploy.
