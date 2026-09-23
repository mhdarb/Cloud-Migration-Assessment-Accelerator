"use client";

import { useState } from "react";
import type { Assessment, Claim } from "@/lib/api";
import { followUpLog } from "@/lib/api";
import { ConfidenceBadge } from "@/components/StatusBadge";
import { EvidenceTrace } from "@/components/EvidenceTrace";

function ReviewCard({
  claim,
  busy,
  onReview,
}: {
  claim: Claim;
  busy: boolean;
  onReview: (
    claimId: string,
    action: string,
    override_value?: string,
    notes?: string
  ) => Promise<boolean>;
}) {
  const [overrideValue, setOverrideValue] = useState(claim.override_value || claim.value);
  const [notes, setNotes] = useState(claim.review_notes || "");

  return (
    <div className="card p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-[240px] flex-1">
          <div className="font-semibold">
            {claim.entity_type}:{claim.entity_key}.{claim.attribute}
          </div>
          <div className="sans mt-1 text-sm">
            Value: <strong>{claim.value}</strong>
          </div>
          <div className="sans mt-2">
            <EvidenceTrace
              evidence={claim.evidence}
              fallbackQuote={claim.evidence_quote}
              unsupported={claim.unsupported}
            />
          </div>
          <div className="mt-2">
            <ConfidenceBadge value={claim.confidence} />
            {claim.unsupported && (
              <span className="badge ml-2 bg-red-100 text-red-700">unsupported</span>
            )}
          </div>
          <label className="sans mt-3 block text-xs uppercase text-[var(--muted)]">
            Override value
          </label>
          <input
            className="input mt-1"
            value={overrideValue}
            onChange={(e) => setOverrideValue(e.target.value)}
          />
          <label className="sans mt-3 block text-xs uppercase text-[var(--muted)]">
            Notes (optional)
          </label>
          <input
            className="input mt-1"
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
            placeholder="Why this decision?"
          />
        </div>
        <div className="flex flex-wrap gap-2">
          <button
            className="btn btn-primary"
            disabled={busy}
            onClick={() => onReview(claim.id, "accept", undefined, notes || undefined)}
          >
            Accept
          </button>
          <button
            className="btn btn-secondary"
            disabled={busy || !overrideValue.trim()}
            onClick={() =>
              onReview(claim.id, "override", overrideValue.trim(), notes || undefined)
            }
          >
            Override
          </button>
          <button
            className="btn btn-secondary"
            disabled={busy}
            onClick={() => onReview(claim.id, "reject", undefined, notes || undefined)}
          >
            Reject
          </button>
        </div>
      </div>
    </div>
  );
}

export function ReviewTab({
  assessment,
  reviewQueue,
  busy,
  onReview,
  onAddFollowUp,
}: {
  assessment: Assessment;
  reviewQueue: Claim[];
  busy: boolean;
  onReview: (
    claimId: string,
    action: string,
    override_value?: string,
    notes?: string
  ) => Promise<boolean>;
  onAddFollowUp: (note: string) => Promise<boolean>;
}) {
  const [followUpNote, setFollowUpNote] = useState("");
  const log = followUpLog(assessment.metrics);

  return (
    <div className="space-y-3">
      {!reviewQueue.length && (
        <div className="card p-6 sans text-sm text-[var(--muted)]">
          No items in the human review queue.
        </div>
      )}
      {reviewQueue.map((c) => (
        <ReviewCard key={c.id} claim={c} busy={busy} onReview={onReview} />
      ))}
      <div className="card p-5">
        <h2 className="mb-3 text-lg">Follow-up learnings</h2>
        <div className="flex flex-wrap gap-2">
          <input
            className="sans min-w-[240px] flex-1 rounded-lg border border-[var(--border)] px-3 py-2 text-sm"
            placeholder="Capture a prompt/rule/template learning…"
            value={followUpNote}
            onChange={(e) => setFollowUpNote(e.target.value)}
          />
          <button
            className="btn btn-secondary"
            disabled={busy || !followUpNote.trim()}
            onClick={async () => {
              if (await onAddFollowUp(followUpNote)) setFollowUpNote("");
            }}
          >
            Add note
          </button>
        </div>
        <ul className="sans mt-3 max-h-40 space-y-1 overflow-auto text-xs text-[var(--muted)]">
          {log.length ? (
            log.map((entry, i) => (
              <li key={i}>
                [{entry.at || "?"}] {entry.event}
                {entry.detail?.note ? ` — ${entry.detail.note}` : ""}
              </li>
            ))
          ) : (
            <li>No follow-up entries yet (rejects/overrides and notes appear here).</li>
          )}
        </ul>
      </div>
    </div>
  );
}
