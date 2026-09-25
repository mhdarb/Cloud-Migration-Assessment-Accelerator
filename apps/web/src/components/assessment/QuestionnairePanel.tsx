"use client";

import { useCallback, useEffect, useState } from "react";
import {
  api,
  QUESTIONNAIRE_ACCEPT,
  type QuestionnaireAnswers,
  type QuestionnaireFile,
} from "@/lib/api";
import { isInFlight } from "@/lib/status";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { AnswerCard } from "@/components/assessment/AnswerCard";

type Working = { id: string; action: "preview" | "original" | "xlsx" | "remove" } | null;

/**
 * Upload a client's questionnaire, preview the evidence-grounded answers, and download it
 * back with the answers written in beside each question (same file format; a PDF comes
 * back as an Excel answer sheet).
 */
export function QuestionnairePanel({
  assessmentId,
  status,
  onError,
}: {
  assessmentId: string;
  status: string;
  onError: (message: string | null) => void;
}) {
  const [files, setFiles] = useState<QuestionnaireFile[]>([]);
  const [uploading, setUploading] = useState(false);
  const [working, setWorking] = useState<Working>(null);
  const [previews, setPreviews] = useState<Record<string, QuestionnaireAnswers>>({});
  const [open, setOpen] = useState<string | null>(null);
  const [confirmRemove, setConfirmRemove] = useState<QuestionnaireFile | null>(null);

  const answerable = status === "completed";
  const running = isInFlight(status) || status === "pending";

  const load = useCallback(async () => {
    try {
      setFiles(await api.questionnaires(assessmentId));
    } catch (e) {
      onError(e instanceof Error ? e.message : "Could not load questionnaires");
    }
  }, [assessmentId, onError]);

  useEffect(() => {
    load();
  }, [load]);

  // A new run can change every answer; drop previews computed against the old one.
  useEffect(() => {
    setPreviews({});
    setOpen(null);
  }, [status]);

  const act = async (id: string, action: NonNullable<Working>["action"], fn: () => Promise<void>) => {
    setWorking({ id, action });
    onError(null);
    try {
      await fn();
    } catch (e) {
      onError(e instanceof Error ? e.message : "Questionnaire action failed");
    } finally {
      setWorking(null);
    }
  };

  const upload = async (file: File) => {
    setUploading(true);
    onError(null);
    try {
      const created = await api.uploadQuestionnaire(assessmentId, file);
      setFiles((prev) => [...prev, created]);
    } catch (e) {
      onError(e instanceof Error ? e.message : "Upload failed");
    } finally {
      setUploading(false);
    }
  };

  const togglePreview = (q: QuestionnaireFile) => {
    if (open === q.id) {
      setOpen(null);
      return;
    }
    if (previews[q.id]) {
      setOpen(q.id);
      return;
    }
    act(q.id, "preview", async () => {
      const result = await api.questionnaireAnswers(assessmentId, q.id);
      setPreviews((prev) => ({ ...prev, [q.id]: result }));
      setOpen(q.id);
    });
  };

  const remove = (q: QuestionnaireFile) =>
    act(q.id, "remove", async () => {
      await api.deleteQuestionnaire(assessmentId, q.id);
      setFiles((prev) => prev.filter((f) => f.id !== q.id));
      if (open === q.id) setOpen(null);
    });

  const busyOn = (id: string) => working?.id === id;
  const answeringLabel = (q: QuestionnaireFile) =>
    `Answering ${q.question_count} question${q.question_count === 1 ? "" : "s"}…`;

  return (
    <div>
      <h2 className="text-xl">Client questionnaires</h2>
      <p className="sans mt-1 text-sm text-[var(--muted)]">
        Upload a client&apos;s questionnaire and download it with an evidence-grounded answer,
        confidence and sources written beside every question. The file is not added to the
        evidence.
      </p>

      <div className="card mt-4 p-4">
        <label className="sans block text-sm">
          <span className="mb-1 block text-xs uppercase text-[var(--muted)]">
            Upload questionnaire
          </span>
          <input
            className="input"
            type="file"
            accept={QUESTIONNAIRE_ACCEPT}
            disabled={uploading}
            onChange={(e) => {
              const file = e.target.files?.[0];
              e.target.value = "";
              if (file) upload(file);
            }}
          />
          <span className="mt-1 block text-xs text-[var(--muted)]">
            {uploading
              ? "Reading questions…"
              : "Excel, CSV, Word, text, Markdown or PDF. Questions are found in a column headed “Question”, or as lines ending in “?” / numbered items."}
          </span>
        </label>
        {!answerable && files.length > 0 && (
          <p className="sans mt-3 text-xs text-amber-700">
            {running
              ? "Answers become available when the current run finishes."
              : "Run the assessment successfully to answer these questionnaires."}
          </p>
        )}
      </div>

      <div className="mt-4 space-y-3">
        {files.map((q) => {
          const preview = previews[q.id];
          const needsReview = preview?.answers.filter((a) => a.needs_human_review).length ?? 0;
          return (
            <div key={q.id} className="card p-4">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div className="min-w-0">
                  <div className="truncate font-semibold" title={q.filename}>
                    {q.filename}
                  </div>
                  <div className="sans text-xs text-[var(--muted)]">
                    {q.question_count} question{q.question_count === 1 ? "" : "s"} ·{" "}
                    {q.file_format.toUpperCase()}
                    {preview
                      ? ` · ${needsReview ? `${needsReview} need review` : "all answers source-linked"}`
                      : ""}
                  </div>
                </div>
                <div className="sans flex flex-wrap gap-2">
                  <button
                    className="btn btn-secondary"
                    disabled={!answerable || busyOn(q.id)}
                    onClick={() => togglePreview(q)}
                  >
                    {busyOn(q.id) && working?.action === "preview"
                      ? answeringLabel(q)
                      : open === q.id
                        ? "Hide answers"
                        : "Preview answers"}
                  </button>
                  <button
                    className="btn btn-primary"
                    disabled={!answerable || busyOn(q.id)}
                    onClick={() =>
                      act(q.id, "original", () =>
                        api.downloadQuestionnaire(assessmentId, q, "original")
                      )
                    }
                  >
                    {busyOn(q.id) && working?.action === "original"
                      ? preview
                        ? "Preparing…"
                        : answeringLabel(q)
                      : `Download answered (.${q.answered_format})`}
                  </button>
                  {q.answered_format !== "xlsx" && (
                    <button
                      className="btn btn-secondary"
                      disabled={!answerable || busyOn(q.id)}
                      onClick={() =>
                        act(q.id, "xlsx", () => api.downloadQuestionnaire(assessmentId, q, "xlsx"))
                      }
                    >
                      {busyOn(q.id) && working?.action === "xlsx" ? "Preparing…" : "Download as Excel"}
                    </button>
                  )}
                  <button
                    className="btn btn-danger"
                    disabled={busyOn(q.id)}
                    onClick={() => setConfirmRemove(q)}
                  >
                    Remove
                  </button>
                </div>
              </div>
              {open === q.id && preview && (
                <div className="mt-4 space-y-3">
                  {preview.answers.map((answer) => (
                    <AnswerCard key={answer.id} answer={answer} />
                  ))}
                </div>
              )}
            </div>
          );
        })}
        {!files.length && (
          <div className="sans text-sm text-[var(--muted)]">No questionnaires uploaded yet.</div>
        )}
      </div>

      <ConfirmDialog
        open={confirmRemove !== null}
        title="Remove questionnaire?"
        message={`“${confirmRemove?.filename ?? ""}” and its answers will be removed. Evidence and findings are not affected.`}
        confirmLabel="Remove"
        danger
        onConfirm={() => {
          const q = confirmRemove;
          setConfirmRemove(null);
          if (q) remove(q);
        }}
        onCancel={() => setConfirmRemove(null)}
      />
    </div>
  );
}
