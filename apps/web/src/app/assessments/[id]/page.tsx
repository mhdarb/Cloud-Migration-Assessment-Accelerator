"use client";

import { Suspense, useState } from "react";
import Link from "next/link";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import { useAssessment } from "@/hooks/useAssessment";
import { parseTab, TABS, type Tab } from "@/lib/tabs";
import { StatusBadge } from "@/components/StatusBadge";
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
    error,
    setError,
    busy,
    onReview,
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
            disabled={
              busy ||
              [
                "ingesting",
                "extracting",
                "reconciling",
                "building_graph",
                "generating_report",
              ].includes(assessment.status)
            }
            onClick={() => {
              if (!window.confirm(`Delete “${assessment.name}”? This cannot be undone.`)) {
                return;
              }
              void onDeleteAssessment().then((ok) => {
                if (ok) router.push("/");
              });
            }}
          >
            Delete
          </button>
          <button className="btn btn-secondary" disabled={busy} onClick={onRerun}>
            Re-run pipeline
          </button>
          {assessment.status === "completed" && assessment.workflow_stage === "review" && (
            <button className="btn btn-primary" disabled={busy} onClick={onCompleteReview}>
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
      {error && (
        <div className="rounded-lg bg-red-50 px-4 py-3 sans text-sm text-red-700">
          {error}
        </div>
      )}

      <div className="flex flex-wrap gap-1 border-b border-[var(--border)]">
        {TABS.map((t) => (
          <button
            key={t}
            className={`tab capitalize ${tab === t ? "active" : ""}`}
            onClick={() => setTab(t)}
          >
            {t}
            {t === "review" && reviewQueue.length > 0 ? ` (${reviewQueue.length})` : ""}
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
      {tab === "report" && <ReportTab assessment={assessment} report={report} />}
      {tab === "review" && (
        <ReviewTab
          assessment={assessment}
          reviewQueue={reviewQueue}
          busy={busy}
          onReview={onReview}
          onAddFollowUp={onAddFollowUp}
        />
      )}
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
