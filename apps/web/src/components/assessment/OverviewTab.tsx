"use client";

import { useState } from "react";
import type { Assessment, Claim, DocumentOut, InfrastructureRecommendation, Usage } from "@/lib/api";
import { UsageCard } from "@/components/assessment/UsageCard";
import { PIPELINE_STAGES } from "@/lib/tabs";
import { isInFlight } from "@/lib/status";
import { ConfirmDialog } from "@/components/ConfirmDialog";

function Metric({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="rounded-lg bg-[#faf9f7] px-3 py-2">
      <div className="text-xs uppercase text-[var(--muted)]">{label}</div>
      <div className="text-xl font-semibold break-all">{value}</div>
    </div>
  );
}

function DocumentRow({
  doc,
  locked,
  busy,
  onRemove,
  onUnlock,
}: {
  doc: DocumentOut;
  locked: boolean;
  busy: boolean;
  onRemove: () => void;
  onUnlock: (password: string) => Promise<boolean>;
}) {
  const [password, setPassword] = useState("");
  const warnings = doc.warnings ?? [];
  return (
    <li className="rounded-lg bg-[#faf9f7] px-3 py-2">
      <div className="flex items-center justify-between gap-3">
        <div className="min-w-0">
          <div className="font-medium break-all">{doc.filename}</div>
          <div className="text-xs text-[var(--muted)]">
            {doc.doc_type} · precedence {doc.precedence}
            {doc.page_count ? ` · ${doc.page_count} pages` : ""}
          </div>
        </div>
        <button type="button" className="btn btn-secondary" disabled={locked} onClick={onRemove}>
          Remove
        </button>
      </div>
      {doc.needs_password ? (
        <form
          className="mt-2 flex flex-wrap items-center gap-2"
          onSubmit={async (e) => {
            e.preventDefault();
            // Cleared either way: the password is only ever held for this one request.
            const value = password;
            setPassword("");
            await onUnlock(value);
          }}
        >
          <span className="text-xs text-amber-800">
            Password-protected — enter its open password to read it.
          </span>
          <input
            id={`unlock-${doc.id}`}
            className="input max-w-[14rem] py-1"
            type="password"
            autoComplete="off"
            placeholder="Document password"
            aria-label={`Password for ${doc.filename}`}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
          <button type="submit" className="btn btn-primary" disabled={locked || busy || !password}>
            Unlock
          </button>
        </form>
      ) : doc.parse_error ? (
        <div className="mt-1 text-xs text-red-700">Couldn’t read this file: {doc.parse_error}</div>
      ) : null}
      {warnings.length > 0 && (
        <ul className="mt-1 list-disc pl-4 text-xs text-[var(--muted)]">
          {warnings.slice(0, 3).map((w) => (
            <li key={w}>{w}</li>
          ))}
          {warnings.length > 3 && <li>+{warnings.length - 3} more</li>}
        </ul>
      )}
    </li>
  );
}

export function OverviewTab({
  assessment,
  usage,
  claims,
  recommendations,
  busy,
  onAddDocuments,
  onRemoveDocument,
  onUnlockDocument,
}: {
  assessment: Assessment;
  usage: Usage | null;
  claims: Claim[];
  recommendations: InfrastructureRecommendation[];
  busy: boolean;
  onAddDocuments: (files: File[]) => void;
  onRemoveDocument: (documentId: string) => void;
  onUnlockDocument: (documentId: string, password: string) => Promise<boolean>;
}) {
  const [pendingRemove, setPendingRemove] = useState<DocumentOut | null>(null);
  const metrics = assessment.metrics || {};
  const reviewCount = claims.filter((c) => c.needs_human_review).length;
  const inferredEdges = Number(metrics.inferred_edges || 0);
  const rag = String(metrics.retrieval_mode || (metrics.rag_queries ? "on" : "off"));
  const stageIdx = PIPELINE_STAGES.indexOf(
    assessment.status as (typeof PIPELINE_STAGES)[number]
  );
  const docsLocked = busy || isInFlight(assessment.status);

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
            <DocumentRow
              key={d.id}
              doc={d}
              locked={docsLocked}
              busy={busy}
              onRemove={() => setPendingRemove(d)}
              onUnlock={(password) => onUnlockDocument(d.id, password)}
            />
          ))}
          {!assessment.documents.length && (
            <li className="text-[var(--muted)]">No documents uploaded.</li>
          )}
        </ul>
        <label className="sans mt-3 block text-xs text-[var(--muted)]">
          Add documents
          <span className="mt-0.5 block text-xs normal-case text-[var(--muted)]">
            Adding files re-runs the pipeline. Review decisions are kept and re-applied.
          </span>
          <input
            className="input mt-1"
            type="file"
            multiple
            disabled={docsLocked}
            accept=".pdf,.docx,.doc,.xlsx,.xlsm,.xls,.csv,.json,.txt,.zip,.md"
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
          <Metric label="Inferred edges" value={inferredEdges} />
        </div>
      </div>
      <UsageCard usage={usage} running={isInFlight(assessment.status)} />
      <ConfirmDialog
        open={pendingRemove !== null}
        title="Remove document"
        message={`Remove ${pendingRemove?.filename}? The pipeline will re-run if other documents remain. Review decisions are kept and re-applied; facts that came only from this document will disappear.`}
        confirmLabel="Remove"
        danger
        onCancel={() => setPendingRemove(null)}
        onConfirm={() => {
          const doc = pendingRemove;
          setPendingRemove(null);
          if (doc) onRemoveDocument(doc.id);
        }}
      />
    </div>
  );
}
