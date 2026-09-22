"use client";

import { useState } from "react";
import type { AssessmentAnswers } from "@/lib/api";
import { EvidenceTrace } from "@/components/EvidenceTrace";

function AnswerCard({
  answer,
}: {
  answer: AssessmentAnswers["answers"][number];
}) {
  return (
    <div className="card p-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <h3 className="font-semibold">{answer.question}</h3>
        <span className="sans text-xs text-[var(--muted)]">
          {Math.round(answer.confidence * 100)}% confidence ·{" "}
          {answer.supported ? "source-linked" : "evidence gap"}
          {answer.answer_source ? ` · ${answer.answer_source}` : ""}
          {answer.origin && answer.origin !== "standard" ? ` · ${answer.origin.replaceAll("_", "-")}` : ""}
        </span>
      </div>
      <p className="sans mt-2 text-sm">{answer.answer}</p>
      {answer.assumptions.map((assumption) => (
        <p className="sans mt-2 text-xs text-amber-700" key={assumption}>
          Review: {assumption}
        </p>
      ))}
      <div className="sans mt-3">
        <EvidenceTrace evidence={answer.evidence} />
      </div>
    </div>
  );
}

export function QuestionsTab({
  answers,
  busy,
  onAsk,
}: {
  answers: AssessmentAnswers | null;
  busy?: boolean;
  onAsk?: (question: string) => Promise<boolean>;
}) {
  const [draft, setDraft] = useState("");
  const all = answers?.answers || [];
  const standard = all.filter((a) => (a.origin || "standard") === "standard");
  const engagement = all.filter((a) => a.origin === "uploaded" || a.origin === "ad_hoc");

  return (
    <div className="space-y-6">
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

      <div>
        <h2 className="text-xl">Engagement questions</h2>
        <p className="sans mt-1 text-sm text-[var(--muted)]">
          From uploaded questionnaires and questions you ask here. Answers use selected
          claims and retrieved source quotes only.
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
              No engagement questions yet. Upload a questionnaire or ask above.
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
