"use client";

import type { AssessmentAnswers } from "@/lib/api";
import { EvidenceTrace } from "@/components/EvidenceTrace";
import { plainText } from "@/lib/text";

export function AnswerCard({
  answer,
}: {
  answer: AssessmentAnswers["answers"][number];
}) {
  const showOrigin =
    answer.origin && answer.origin !== "standard" && answer.origin !== "questionnaire";
  return (
    <div className="card p-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <h3 className="font-semibold">
          {answer.position ? `${answer.position}. ` : ""}
          {answer.question}
        </h3>
        <span className="sans text-xs text-[var(--muted)]">
          {Math.round(answer.confidence * 100)}% confidence ·{" "}
          {answer.supported ? "source-linked" : "evidence gap"}
          {answer.answer_source ? ` · ${answer.answer_source}` : ""}
          {showOrigin ? ` · ${answer.origin!.replaceAll("_", "-")}` : ""}
        </span>
      </div>
      <p className="sans mt-2 whitespace-pre-line text-sm">{plainText(answer.answer)}</p>
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
