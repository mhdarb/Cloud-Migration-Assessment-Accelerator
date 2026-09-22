# Web UI

Next.js App Router UI for the Cloud Migration Assessment Accelerator. It talks to the FastAPI app; it does not run extraction itself.

Install and run both API and UI: **[docs/SETUP.md](../../docs/SETUP.md)**. Pipeline and tabs: **[docs/ARCHITECTURE.md](../../docs/ARCHITECTURE.md)**.

## Run

```bash
cp .env.local.example .env.local
# NEXT_PUBLIC_API_URL=http://localhost:8000
npm install
npm run dev
```

Open [http://localhost:3000](http://localhost:3000). The API must already be on port 8000 (or match `NEXT_PUBLIC_API_URL`).

## Layout

| Path | Role |
|------|------|
| [`src/app/page.tsx`](src/app/page.tsx) | Create assessment, upload files, list |
| [`src/app/assessments/[id]/page.tsx`](src/app/assessments/[id]/page.tsx) | Detail tabs |
| [`src/lib/api.ts`](src/lib/api.ts) | Typed client for `/assessments/*` |
| [`src/components/DependencyGraph.tsx`](src/components/DependencyGraph.tsx) | React Flow graph + blast radius |

Tabs: Overview, Questions, Sizing, Findings, Graph, Report, Review. Evidence (filename / page / quote) is shown wherever the API returns `evidence`.
