"use client";

import { useState } from "react";
import type { AssessmentAnswers } from "@/lib/api";
import { AnswerCard } from "@/components/assessment/AnswerCard";
import { QuestionnairePanel } from "@/components/assessment/QuestionnairePanel";

export function QuestionsTab({
  assessmentId,
  status,
  answers,
  busy,
  onAsk,
  onError,
}: {
  assessmentId: string;
  status: string;
  answers: AssessmentAnswers | null;
  busy?: boolean;
  onAsk?: (question: string) => Promise<boolean>;
  onError: (message: string | null) => void;
}) {
  const [draft, setDraft] = useState("");
  const all = answers?.answers || [];
  const standard = all.filter((a) => (a.origin || "standard") === "standard");
  const dynamic = all.filter((a) => a.origin === "dynamic");
  const engagement = all.filter((a) => a.origin === "uploaded" || a.origin === "ad_hoc");

  return (
    <div className="space-y-6">
      <QuestionnairePanel assessmentId={assessmentId} status={status} onError={onError} />

      <div>
        <div className="flex items-center justify-between">
          <h2 className="text-xl">Standard migration assessment questions</h2>
          <span className="sans text-xs text-[var(--muted)]">
            {answers?.question_set || "migration-readiness-v1"}
          </span>
        </div>
        <div className="mt-4 space-y-4">
          {standard.map((answer) => (
            <AnswerCard key={answer.id} answer={answer} />
          ))}
          {!standard.length && (
            <div className="card p-5 sans text-sm text-[var(--muted)]">
              Assessment questions appear after extraction completes.
            </div>
          )}
        </div>
      </div>

      {dynamic.length > 0 && (
        <div>
          <h2 className="text-xl">Estate-specific questions</h2>
          <p className="sans mt-1 text-sm text-[var(--muted)]">
            Generated from signals specific to this estate (e.g. unsupported operating
            systems, sizing gaps) — not part of the standard question set.
          </p>
          <div className="mt-4 space-y-4">
            {dynamic.map((answer) => (
              <AnswerCard key={answer.id} answer={answer} />
            ))}
          </div>
        </div>
      )}

      <div>
        <h2 className="text-xl">Engagement questions</h2>
        <p className="sans mt-1 text-sm text-[var(--muted)]">
          Questions found in questionnaire documents among the evidence, and questions you
          ask here. Answers use selected claims and retrieved source quotes only.
        </p>
        {onAsk && (
          <div className="card mt-4 p-4">
            <label className="sans mb-1 block text-xs uppercase text-[var(--muted)]">
              Ask a question
            </label>
            <textarea
              className="input min-h-[72px]"
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              placeholder="e.g. Which applications are business critical?"
              maxLength={500}
            />
            <button
              className="btn btn-primary mt-3"
              disabled={busy || !draft.trim()}
              onClick={async () => {
                if (onAsk && (await onAsk(draft))) setDraft("");
              }}
            >
              Ask
            </button>
          </div>
        )}
        <div className="mt-4 space-y-4">
          {engagement.map((answer) => (
            <AnswerCard key={answer.id} answer={answer} />
          ))}
          {!engagement.length && (
            <div className="sans text-sm text-[var(--muted)]">
              No engagement questions yet. Ask one above.
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
