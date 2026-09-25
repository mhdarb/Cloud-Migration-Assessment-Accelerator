"use client";

import { useMemo, useState } from "react";
import type {
  Assessment,
  Claim,
  ClaimReviewInput,
  Conflict,
  DependencyEdge,
  InfrastructureRecommendation,
  ReviewStatus,
} from "@/lib/api";
import { followUpLog } from "@/lib/api";
import { ConfidenceBadge } from "@/components/StatusBadge";
import { EvidenceTrace } from "@/components/EvidenceTrace";

type ReviewFn = (
  claimId: string,
  action: string,
  override_value?: string,
  notes?: string
) => Promise<boolean>;

function claimValue(c: Claim): string {
  return c.override_value || c.value;
}

function ReviewCard({
  claim,
  conflict,
  siblings,
  selected,
  onToggle,
  busy,
  onReview,
  onDismissConflict,
}: {
  claim: Claim;
  conflict: Conflict | undefined;
  siblings: Claim[];
  selected: boolean;
  onToggle: () => void;
  busy: boolean;
  onReview: ReviewFn;
  onDismissConflict: (conflictId: string, notes?: string) => Promise<boolean>;
}) {
  const [overrideValue, setOverrideValue] = useState(claim.override_value || claim.value);
  const [notes, setNotes] = useState(claim.review_notes || "");
  const note = notes.trim() || undefined;

  return (
    <div className="card p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <label className="flex min-w-[240px] flex-1 gap-3">
          <input
            type="checkbox"
            className="mt-1"
            checked={selected}
            onChange={onToggle}
            aria-label={`Select ${claim.entity_key}.${claim.attribute} for bulk action`}
          />
          <span className="block flex-1">
            <span className="block font-semibold">
              {claim.entity_type}:{claim.entity_key}.{claim.attribute}
            </span>
            <span className="sans mt-1 block text-sm">
              Value: <strong>{claim.value}</strong>
            </span>
          </span>
        </label>
        <div className="flex flex-wrap gap-2">
          <button
            className="btn btn-primary"
            disabled={busy}
            onClick={() => onReview(claim.id, "accept", undefined, note)}
          >
            Accept
          </button>
          <button
            className="btn btn-secondary"
            disabled={busy || !overrideValue.trim()}
            onClick={() => onReview(claim.id, "override", overrideValue.trim(), note)}
          >
            Override
          </button>
          <button
            className="btn btn-secondary"
            disabled={busy}
            onClick={() => onReview(claim.id, "reject", undefined, note)}
          >
            Reject
          </button>
        </div>
      </div>

      {conflict && (
        <div className="sans mt-3 rounded-lg border border-[var(--border)] bg-[#fdf6ec] p-3 text-sm">
          <div className="font-medium">Sources disagree on this value</div>
          <ul className="mt-1 space-y-0.5 text-[var(--muted)]">
            {[claim, ...siblings].map((c) => (
              <li key={c.id}>
                <strong className="text-[var(--foreground)]">{claimValue(c)}</strong>
                {c.id === conflict.selected_claim_id ? " — currently selected" : ""}
                {c.id === claim.id ? " (this card)" : ""}
                {c.evidence?.[0]?.filename ? ` · ${c.evidence[0].filename}` : ""}
              </li>
            ))}
          </ul>
          <p className="mt-1 text-xs text-[var(--muted)]">
            Accepting one value settles the conflict. If none is right, dismiss it and the
            attribute is recorded as unknown.
          </p>
          <button
            className="btn btn-secondary mt-2"
            disabled={busy}
            onClick={() => onDismissConflict(conflict.id, note)}
          >
            Dismiss conflict — none is correct
          </button>
        </div>
      )}

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
      <div className="sans mt-3 grid gap-3 sm:grid-cols-2">
        <label className="block text-xs uppercase text-[var(--muted)]">
          Override value
          <input
            className="input mt-1 normal-case"
            value={overrideValue}
            onChange={(e) => setOverrideValue(e.target.value)}
          />
        </label>
        <label className="block text-xs uppercase text-[var(--muted)]">
          Notes (optional)
          <input
            className="input mt-1 normal-case"
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
            placeholder="Why this decision?"
          />
        </label>
      </div>
    </div>
  );
}

function DecisionRow({
  title,
  detail,
  confidence,
  acceptLabel,
  busy,
  onDecide,
  children,
}: {
  title: React.ReactNode;
  detail?: React.ReactNode;
  confidence?: number;
  acceptLabel: string;
  busy: boolean;
  onDecide: (action: "accept" | "reject", notes?: string) => Promise<boolean>;
  children?: React.ReactNode;
}) {
  const [notes, setNotes] = useState("");
  const note = notes.trim() || undefined;
  return (
    <div className="card p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-[240px] flex-1">
          <div className="font-semibold">{title}</div>
          {detail && <div className="sans mt-1 text-sm text-[var(--muted)]">{detail}</div>}
          {confidence != null && (
            <div className="mt-2">
              <ConfidenceBadge value={confidence} />
            </div>
          )}
          {children}
          <label className="sans mt-3 block text-xs uppercase text-[var(--muted)]">
            Notes (optional)
            <input
              className="input mt-1 normal-case"
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              placeholder="Why this decision?"
            />
          </label>
        </div>
        <div className="flex flex-wrap gap-2">
          <button className="btn btn-primary" disabled={busy} onClick={() => onDecide("accept", note)}>
            {acceptLabel}
          </button>
          <button className="btn btn-secondary" disabled={busy} onClick={() => onDecide("reject", note)}>
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
  claims,
  conflicts,
  reviewEdges,
  recommendations,
  reviewStatus,
  reviewer,
  setReviewer,
  busy,
  onReview,
  onReviewBatch,
  onDismissConflict,
  onReviewEdge,
  onReviewRecommendation,
  onAddFollowUp,
}: {
  assessment: Assessment;
  reviewQueue: Claim[];
  claims: Claim[];
  conflicts: Conflict[];
  reviewEdges: DependencyEdge[];
  recommendations: InfrastructureRecommendation[];
  reviewStatus: ReviewStatus | null;
  reviewer: string;
  setReviewer: (name: string) => void;
  busy: boolean;
  onReview: ReviewFn;
  onReviewBatch: (reviews: ClaimReviewInput[]) => Promise<boolean>;
  onDismissConflict: (conflictId: string, notes?: string) => Promise<boolean>;
  onReviewEdge: (edgeId: string, action: "accept" | "reject", notes?: string) => Promise<boolean>;
  onReviewRecommendation: (
    recommendationId: string,
    action: "accept" | "reject",
    notes?: string
  ) => Promise<boolean>;
  onAddFollowUp: (note: string) => Promise<boolean>;
}) {
  const [followUpNote, setFollowUpNote] = useState("");
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const log = followUpLog(assessment.metrics);

  const claimsById = useMemo(() => new Map(claims.map((c) => [c.id, c])), [claims]);
  const openConflictByClaim = useMemo(() => {
    const map = new Map<string, Conflict>();
    for (const cf of conflicts) {
      if (cf.status !== "open") continue;
      for (const cid of cf.claim_ids) map.set(cid, cf);
    }
    return map;
  }, [conflicts]);
  const pendingRecs = recommendations.filter((r) => r.needs_human_review);

  // Selection only ever contains claims still in the queue (decided ones drop out).
  const queueIds = new Set(reviewQueue.map((c) => c.id));
  const selected = [...picked].filter((cid) => queueIds.has(cid));
  const toggle = (cid: string) =>
    setPicked((prev) => {
      const next = new Set(prev);
      if (next.has(cid)) next.delete(cid);
      else next.add(cid);
      return next;
    });
  const bulk = async (action: "accept" | "reject") => {
    const ok = await onReviewBatch(selected.map((claim_id) => ({ claim_id, action })));
    if (ok) setPicked(new Set());
  };

  return (
    <div className="space-y-4">
      <div className="card sans flex flex-wrap items-end justify-between gap-4 p-4">
        <label className="block text-xs uppercase text-[var(--muted)]">
          Reviewing as
          <input
            className="input mt-1 normal-case"
            value={reviewer}
            onChange={(e) => setReviewer(e.target.value)}
            placeholder="Your name or email (recorded on each decision)"
          />
        </label>
        {reviewStatus && (
          <div
            role="status"
            className={`text-sm ${reviewStatus.clear ? "text-[var(--success)]" : "text-[var(--muted)]"}`}
          >
            {reviewStatus.clear
              ? "Nothing left to review — you can complete the review."
              : `Still needs a decision: ${reviewStatus.summary}.`}
          </div>
        )}
      </div>

      <section className="space-y-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h2 className="text-lg">Claims ({reviewQueue.length})</h2>
          {reviewQueue.length > 0 && (
            <div className="sans flex flex-wrap items-center gap-2 text-sm">
              <label className="flex items-center gap-2">
                <input
                  type="checkbox"
                  checked={selected.length === reviewQueue.length}
                  onChange={(e) =>
                    setPicked(e.target.checked ? new Set(reviewQueue.map((c) => c.id)) : new Set())
                  }
                />
                Select all
              </label>
              <button
                className="btn btn-primary"
                disabled={busy || !selected.length}
                onClick={() => bulk("accept")}
              >
                Accept selected ({selected.length})
              </button>
              <button
                className="btn btn-secondary"
                disabled={busy || !selected.length}
                onClick={() => bulk("reject")}
              >
                Reject selected
              </button>
            </div>
          )}
        </div>
        {!reviewQueue.length && (
          <div className="card sans p-5 text-sm text-[var(--muted)]">No claims need review.</div>
        )}
        {reviewQueue.map((c) => {
          const conflict = openConflictByClaim.get(c.id);
          const siblings = conflict
            ? conflict.claim_ids
                .filter((cid) => cid !== c.id)
                .map((cid) => claimsById.get(cid))
                .filter((x): x is Claim => Boolean(x))
            : [];
          return (
            <ReviewCard
              key={c.id}
              claim={c}
              conflict={conflict}
              siblings={siblings}
              selected={selected.includes(c.id)}
              onToggle={() => toggle(c.id)}
              busy={busy}
              onReview={onReview}
              onDismissConflict={onDismissConflict}
            />
          );
        })}
      </section>

      {reviewEdges.length > 0 && (
        <section className="space-y-3">
          <h2 className="text-lg">Dependencies ({reviewEdges.length})</h2>
          <p className="sans text-sm text-[var(--muted)]">
            Low-confidence or inferred relationships. Rejected ones are removed from the
            dependency graph and the report.
          </p>
          {reviewEdges.map((e) => (
            <DecisionRow
              key={e.id}
              title={
                <>
                  {e.source_key} <span className="text-[var(--muted)]">—{e.rel_type.replaceAll("_", " ")}→</span>{" "}
                  {e.target_key}
                </>
              }
              detail={e.rationale || (e.evidence_quote ? `“${e.evidence_quote}”` : "No source evidence (inferred)")}
              confidence={e.confidence}
              acceptLabel="Accept"
              busy={busy}
              onDecide={(action, notes) => onReviewEdge(e.id, action, notes)}
            />
          ))}
        </section>
      )}

      {pendingRecs.length > 0 && (
        <section className="space-y-3">
          <h2 className="text-lg">Sizing recommendations ({pendingRecs.length})</h2>
          <p className="sans text-sm text-[var(--muted)]">
            Flagged because the sizing relied on assumptions, or the OS isn&apos;t supported.
            A sign-off applies to this exact SKU — if new evidence changes the sizing, it
            comes back for review.
          </p>
          {pendingRecs.map((r) => (
            <DecisionRow
              key={r.id}
              title={
                <>
                  {r.server_key} → {r.recommended_sku && r.recommended_sku !== "none" ? r.recommended_sku : "no SKU"}
                </>
              }
              detail={r.result?.explanation}
              confidence={r.confidence}
              acceptLabel="Sign off"
              busy={busy}
              onDecide={(action, notes) => onReviewRecommendation(r.id, action, notes)}
            >
              {r.result?.assumptions?.length ? (
                <ul className="sans mt-2 list-disc pl-5 text-xs text-[var(--muted)]">
                  {r.result.assumptions.map((a) => (
                    <li key={a}>{a}</li>
                  ))}
                </ul>
              ) : null}
            </DecisionRow>
          ))}
        </section>
      )}

      <div className="card p-5">
        <h2 className="mb-3 text-lg">Review activity &amp; learnings</h2>
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
        <ul className="sans mt-3 max-h-48 space-y-1 overflow-auto text-xs text-[var(--muted)]">
          {log.length ? (
            [...log].reverse().map((entry, i) => (
              <li key={i}>
                [{entry.at || "?"}] {entry.event?.replaceAll("_", " ")}
                {entry.detail?.reviewed_by ? ` by ${entry.detail.reviewed_by}` : ""}
                {(entry.detail?.note ?? entry.detail?.notes)
                  ? ` — ${entry.detail?.note ?? entry.detail?.notes}`
                  : ""}
              </li>
            ))
          ) : (
            <li>No activity yet — review decisions and notes appear here.</li>
          )}
        </ul>
      </div>
    </div>
  );
}
