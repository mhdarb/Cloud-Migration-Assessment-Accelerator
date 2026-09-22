import type { Assessment, Claim, InfrastructureRecommendation } from "@/lib/api";
import { PIPELINE_STAGES } from "@/lib/tabs";

const IN_FLIGHT = new Set([
  "ingesting",
  "extracting",
  "reconciling",
  "building_graph",
  "generating_report",
]);

function Metric({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="rounded-lg bg-[#faf9f7] px-3 py-2">
      <div className="text-xs uppercase text-[var(--muted)]">{label}</div>
      <div className="text-xl font-semibold break-all">{value}</div>
    </div>
  );
}

export function OverviewTab({
  assessment,
  claims,
  recommendations,
  busy,
  onAddDocuments,
  onRemoveDocument,
}: {
  assessment: Assessment;
  claims: Claim[];
  recommendations: InfrastructureRecommendation[];
  busy: boolean;
  onAddDocuments: (files: File[]) => void;
  onRemoveDocument: (documentId: string) => void;
}) {
  const metrics = assessment.metrics || {};
  const reviewCount = claims.filter((c) => c.needs_human_review).length;
  const graphSource = metrics.neo4j_synced ? "Neo4j" : "Postgres";
  const rag = String(metrics.retrieval_mode || (metrics.rag_queries ? "on" : "off"));
  const stageIdx = PIPELINE_STAGES.indexOf(
    assessment.status as (typeof PIPELINE_STAGES)[number]
  );
  const docsLocked = busy || IN_FLIGHT.has(assessment.status);

  return (
    <div className="grid gap-4 md:grid-cols-2">
      <div className="card p-5 md:col-span-2">
        <h2 className="mb-3 text-lg">Pipeline</h2>
        <div className="flex flex-wrap gap-2">
          {PIPELINE_STAGES.map((stage, i) => {
            const active =
              assessment.status === "failed"
                ? false
                : stageIdx >= 0
                  ? i <= stageIdx
                  : false;
            return (
              <div
                key={stage}
                className="sans rounded-full px-3 py-1 text-xs font-semibold capitalize"
                style={{
                  background: active ? "var(--accent-soft)" : "#f3f4f6",
                  color: active ? "var(--accent)" : "#6b7280",
                }}
              >
                {i + 1}. {stage.replaceAll("_", " ")}
              </div>
            );
          })}
          {assessment.status === "failed" && (
            <div className="sans rounded-full bg-red-50 px-3 py-1 text-xs font-semibold text-red-700">
              Failed
            </div>
          )}
        </div>
      </div>
      <div className="card p-5">
        <h2 className="mb-3 text-lg">Documents</h2>
        <ul className="sans space-y-2 text-sm">
          {assessment.documents.map((d) => (
            <li
              key={d.id}
              className="flex items-center justify-between rounded-lg bg-[#faf9f7] px-3 py-2"
            >
              <div>
                <div className="font-medium">{d.filename}</div>
                <div className="text-xs text-[var(--muted)]">
                  {d.doc_type} · precedence {d.precedence}
                  {d.page_count ? ` · ${d.page_count} pages` : ""}
                </div>
              </div>
              <button
                type="button"
                className="btn btn-secondary"
                disabled={docsLocked}
                onClick={() => {
                  if (!window.confirm(`Remove ${d.filename}? The pipeline will re-run if other documents remain.`)) {
                    return;
                  }
                  onRemoveDocument(d.id);
                }}
              >
                Remove
              </button>
            </li>
          ))}
          {!assessment.documents.length && (
            <li className="text-[var(--muted)]">No documents uploaded.</li>
          )}
        </ul>
        <label className="sans mt-3 block text-xs text-[var(--muted)]">
          Add documents
          <input
            className="input mt-1"
            type="file"
            multiple
            disabled={docsLocked}
            accept=".pdf,.docx,.doc,.xlsx,.xlsm,.txt,.zip,.md"
            onChange={(e) => {
              const files = e.target.files ? Array.from(e.target.files) : [];
              e.target.value = "";
              if (files.length) onAddDocuments(files);
            }}
          />
        </label>
      </div>
      <div className="card p-5">
        <h2 className="mb-3 text-lg">Snapshot</h2>
        <div className="sans grid grid-cols-2 gap-3 text-sm">
          <Metric label="Documents" value={assessment.documents.length} />
          <Metric label="Claims" value={claims.length} />
          <Metric label="Recommendations" value={recommendations.length} />
          <Metric label="Review queue" value={reviewCount} />
          <Metric label="RAG" value={rag} />
          <Metric label="Graph" value={String(graphSource)} />
        </div>
      </div>
    </div>
  );
}
