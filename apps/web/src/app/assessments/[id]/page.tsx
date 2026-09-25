"use client";

import { Suspense, useState } from "react";
import Link from "next/link";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import { useAssessment } from "@/hooks/useAssessment";
import { parseTab, TABS, type Tab } from "@/lib/tabs";
import { isInFlight, isTerminal } from "@/lib/status";
import { StatusBadge } from "@/components/StatusBadge";
import { RuntimeTimer } from "@/components/RuntimeTimer";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { ErrorBanner } from "@/components/ErrorBanner";
import { OverviewTab } from "@/components/assessment/OverviewTab";
import { QuestionsTab } from "@/components/assessment/QuestionsTab";
import { SizingTab } from "@/components/assessment/SizingTab";
import { FindingsTab } from "@/components/assessment/FindingsTab";
import { GraphTab } from "@/components/assessment/GraphTab";
import { ReportTab } from "@/components/assessment/ReportTab";
import { ReviewTab } from "@/components/assessment/ReviewTab";

function AssessmentDetail() {
  const params = useParams<{ id: string }>();
  const id = params.id;
  const router = useRouter();
  const searchParams = useSearchParams();
  const tab = parseTab(searchParams.get("tab"));
  const {
    assessment,
    claims,
    entities,
    graph,
    report,
    conflicts,
    answers,
    recommendations,
    reviewEdges,
    reviewStatus,
    reviewer,
    setReviewer,
    error,
    setError,
    busy,
    onReview,
    onReviewBatch,
    onDismissConflict,
    onReviewEdge,
    onReviewRecommendation,
    onRerun,
    onCompleteReview,
    onAddFollowUp,
    onAskQuestion,
    onRename,
    onDeleteAssessment,
    onAddDocuments,
    onRemoveDocument,
  } = useAssessment(id);

  const [editing, setEditing] = useState(false);
  const [draftName, setDraftName] = useState("");
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [confirmRerun, setConfirmRerun] = useState(false);

  const setTab = (next: Tab) => {
    router.replace(`/assessments/${id}?tab=${next}`, { scroll: false });
  };

  if (!assessment) {
    return (
      <div className="sans text-sm text-[var(--muted)]">
        {error || "Loading assessment…"}
      </div>
    );
  }

  const reviewQueue = claims.filter((c) => c.needs_human_review);
  // Everything that blocks sign-off, not just claims (conflicts, edges, sizing too).
  const pendingReview = reviewStatus
    ? reviewStatus.pending_claims +
      reviewStatus.open_conflicts +
      reviewStatus.pending_edges +
      reviewStatus.pending_recommendations
    : reviewQueue.length;
  const reviewBlocked = Boolean(reviewStatus?.enforce_review && !reviewStatus.clear);

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <Link href="/" className="sans text-xs text-[var(--muted)] hover:text-[var(--accent)]">
            ← All assessments
          </Link>
          <h1 className="mt-1 text-3xl">{assessment.name}</h1>
          {editing ? (
            <div className="mt-2 flex flex-wrap gap-2">
              <input
                className="input sans"
                value={draftName}
                onChange={(e) => setDraftName(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") {
                    void onRename(draftName).then((ok) => {
                      if (ok) setEditing(false);
                    });
                  }
                  if (e.key === "Escape") setEditing(false);
                }}
                autoFocus
              />
              <button
                className="btn btn-primary"
                disabled={busy}
                onClick={() =>
                  void onRename(draftName).then((ok) => {
                    if (ok) setEditing(false);
                  })
                }
              >
                Save
              </button>
              <button className="btn btn-secondary" onClick={() => setEditing(false)}>
                Cancel
              </button>
            </div>
          ) : null}
          <div className="mt-2 flex flex-wrap items-center gap-3">
            <StatusBadge status={assessment.status} />
            <RuntimeTimer
              key={assessment.pipeline_started_at ?? "not-started"}
              runtimeSeconds={assessment.runtime_seconds}
              status={assessment.status}
              running={!isTerminal(assessment.status) && !assessment.pipeline_finished_at}
            />
            <span className="sans text-sm text-[var(--muted)]">
              Workflow: {assessment.workflow_stage.replaceAll("_", " ")}
            </span>
          </div>
        </div>
        <div className="flex flex-wrap gap-2">
          <button
            className="btn btn-secondary"
            disabled={busy}
            onClick={() => {
              setDraftName(assessment.name);
              setEditing(true);
            }}
          >
            Rename
          </button>
          <button
            className="btn btn-secondary"
            disabled={busy || isInFlight(assessment.status)}
            onClick={() => setConfirmDelete(true)}
          >
            Delete
          </button>
          <button
            className="btn btn-secondary"
            disabled={busy || isInFlight(assessment.status)}
            onClick={() => setConfirmRerun(true)}
          >
            Re-run pipeline
          </button>
          {assessment.status === "completed" && assessment.workflow_stage === "review" && (
            <button
              className="btn btn-primary"
              disabled={busy || reviewBlocked}
              title={reviewBlocked ? `Still needs a decision: ${reviewStatus?.summary}` : undefined}
              onClick={onCompleteReview}
            >
              Complete review
            </button>
          )}
        </div>
      </div>

      {assessment.error_message && (
        <div className="rounded-lg bg-red-50 px-4 py-3 sans text-sm text-red-700">
          {assessment.error_message}
        </div>
      )}
      {error && <ErrorBanner message={error} onDismiss={() => setError(null)} />}

      <div className="flex flex-wrap gap-1 border-b border-[var(--border)]">
        {TABS.map((t) => (
          <button
            key={t}
            className={`tab capitalize ${tab === t ? "active" : ""}`}
            onClick={() => setTab(t)}
          >
            {t}
            {t === "review" && pendingReview > 0 ? ` (${pendingReview})` : ""}
          </button>
        ))}
      </div>

      {tab === "overview" && (
        <OverviewTab
          assessment={assessment}
          claims={claims}
          recommendations={recommendations}
          busy={busy}
          onAddDocuments={onAddDocuments}
          onRemoveDocument={onRemoveDocument}
        />
      )}
      {tab === "questions" && (
        <QuestionsTab answers={answers} busy={busy} onAsk={onAskQuestion} />
      )}
      {tab === "sizing" && <SizingTab recommendations={recommendations} />}
      {tab === "findings" && (
        <FindingsTab entities={entities} claims={claims} conflicts={conflicts} />
      )}
      {tab === "graph" && (
        <GraphTab id={id} graph={graph} onError={setError} />
      )}
      {tab === "report" && (
        <ReportTab assessment={assessment} report={report} recommendations={recommendations} />
      )}
      {tab === "review" && (
        <ReviewTab
          assessment={assessment}
          reviewQueue={reviewQueue}
          claims={claims}
          conflicts={conflicts}
          reviewEdges={reviewEdges}
          recommendations={recommendations}
          reviewStatus={reviewStatus}
          reviewer={reviewer}
          setReviewer={setReviewer}
          busy={busy}
          onReview={onReview}
          onReviewBatch={onReviewBatch}
          onDismissConflict={onDismissConflict}
          onReviewEdge={onReviewEdge}
          onReviewRecommendation={onReviewRecommendation}
          onAddFollowUp={onAddFollowUp}
        />
      )}
      <ConfirmDialog
        open={confirmRerun}
        title="Re-run pipeline"
        message={
          "Re-running rebuilds every finding from the documents. Your review decisions are " +
          "kept and re-applied automatically; items whose source evidence changed will need " +
          "review again, and you'll need to complete the review again."
        }
        confirmLabel="Re-run"
        onCancel={() => setConfirmRerun(false)}
        onConfirm={() => {
          setConfirmRerun(false);
          void onRerun();
        }}
      />
      <ConfirmDialog
        open={confirmDelete}
        title="Delete assessment"
        message={`Delete "${assessment.name}"? This cannot be undone.`}
        confirmLabel="Delete"
        danger
        onCancel={() => setConfirmDelete(false)}
        onConfirm={() => {
          setConfirmDelete(false);
          void onDeleteAssessment().then((ok) => {
            if (ok) router.push("/");
          });
        }}
      />
    </div>
  );
}

export default function AssessmentPage() {
  return (
    <Suspense fallback={<div className="sans text-sm text-[var(--muted)]">Loading assessment…</div>}>
      <AssessmentDetail />
    </Suspense>
  );
}
