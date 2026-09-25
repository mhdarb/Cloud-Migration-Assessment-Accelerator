"use client";

import { useCallback, useEffect, useState } from "react";
import {
  api,
  Assessment,
  AssessmentAnswers,
  Claim,
  ClaimReviewInput,
  Conflict,
  DependencyEdge,
  Entity,
  GraphOut,
  InfrastructureRecommendation,
  Report,
  ReviewStatus,
} from "@/lib/api";
import { isTerminal } from "@/lib/status";

const REVIEWER_KEY = "cmaa.reviewer";

/** Sections whose empty state must not be confused with "still loading". */
export type DetailSection = "answers" | "recommendations";
/** No entry = the first load is still in flight. */
export type LoadState = Partial<Record<DetailSection, "loaded" | "failed">>;

export function useAssessment(id: string) {
  const [assessment, setAssessment] = useState<Assessment | null>(null);
  const [claims, setClaims] = useState<Claim[]>([]);
  const [entities, setEntities] = useState<Entity[]>([]);
  const [graph, setGraph] = useState<GraphOut | null>(null);
  const [report, setReport] = useState<Report | null>(null);
  const [conflicts, setConflicts] = useState<Conflict[]>([]);
  const [answers, setAnswers] = useState<AssessmentAnswers | null>(null);
  const [recommendations, setRecommendations] = useState<
    InfrastructureRecommendation[]
  >([]);
  const [reviewEdges, setReviewEdges] = useState<DependencyEdge[]>([]);
  const [reviewStatus, setReviewStatus] = useState<ReviewStatus | null>(null);
  const [reviewer, setReviewerState] = useState("");
  const [loadState, setLoadState] = useState<LoadState>({});
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  // The reviewer's name is recorded on every decision for the audit trail. Remembered per
  // browser as a convenience only (storage can be unavailable, e.g. private windows).
  useEffect(() => {
    try {
      setReviewerState(localStorage.getItem(REVIEWER_KEY) ?? "");
    } catch {
      /* storage unavailable */
    }
  }, []);
  const setReviewer = (name: string) => {
    setReviewerState(name);
    try {
      localStorage.setItem(REVIEWER_KEY, name);
    } catch {
      /* storage unavailable */
    }
  };
  const reviewerOrUndefined = () => reviewer.trim() || undefined;
  const [pollGeneration, setPollGeneration] = useState(0);

  const loadCore = useCallback(async () => {
    const a = await api.getAssessment(id);
    setAssessment(a);
    return a;
  }, [id]);

  const loadDetails = useCallback(async (status: string) => {
    const failed: string[] = [];
    // Each result is applied the moment it arrives. Waiting for all of them (as a single
    // Promise.allSettled did) held every tab hostage to the slowest request — question
    // answers can take many seconds with retrieval + an LLM — so on a refresh the Sizing
    // tab sat empty and the standard questions looked gone until everything returned.
    const track = <T,>(
      request: Promise<T>,
      section: DetailSection | null,
      label: string,
      setter: (value: T) => void
    ) =>
      request.then(
        (value) => {
          setter(value);
          if (section) setLoadState((prev) => ({ ...prev, [section]: "loaded" }));
        },
        () => {
          failed.push(label);
          // Keep data from an earlier successful load; only report failure if there's none.
          if (section) setLoadState((prev) => (prev[section] === "loaded" ? prev : { ...prev, [section]: "failed" }));
        }
      );
    await Promise.all([
      track(api.claims(id), null, "claims", setClaims),
      track(api.entities(id), null, "entities", setEntities),
      track(api.graph(id), null, "graph", setGraph),
      track(api.conflicts(id), null, "conflicts", setConflicts),
      track(api.assessmentQuestions(id), "answers", "questions", setAnswers),
      track(api.recommendations(id), "recommendations", "recommendations", setRecommendations),
      track(api.edges(id, true), null, "dependency edges", setReviewEdges),
      track(api.reviewStatus(id), null, "review status", setReviewStatus),
      api.report(id).then(setReport, () => {
        setReport(null);
        if (isTerminal(status)) failed.push("report");
      }),
    ]);

    if (failed.length && isTerminal(status)) {
      setError(`Could not load ${failed.join(", ")}`);
    }
  }, [id]);

  useEffect(() => {
    let cancelled = false;
    let timeout: ReturnType<typeof setTimeout> | undefined;
    let delay = 2000;
    const tick = async () => {
      try {
        const a = await loadCore();
        if (cancelled) return;
        setError(null);
        await loadDetails(a.status);
        if (cancelled) return;
        if (isTerminal(a.status)) return;
        delay = Math.min(Math.round(delay * 1.4), 12000);
        timeout = setTimeout(tick, delay);
      } catch (e) {
        if (!cancelled) {
          setError(e instanceof Error ? e.message : "Failed to load");
          timeout = setTimeout(tick, delay);
        }
      }
    };
    tick();
    return () => {
      cancelled = true;
      if (timeout) clearTimeout(timeout);
    };
  }, [loadCore, loadDetails, pollGeneration]);

  const restartPolling = () => setPollGeneration((n) => n + 1);

  // Every action below needs the same busy/error bracketing -- run it, report failure via
  // `error`, always clear `busy` -- so that's centralized here once instead of repeated
  // per action. Returns whether `fn` succeeded, for actions whose caller needs to know
  // (e.g. clear a draft field only on success).
  const runAction = useCallback(async (fn: () => Promise<void>, fallbackMessage: string) => {
    setBusy(true);
    setError(null);
    try {
      await fn();
      return true;
    } catch (e) {
      setError(e instanceof Error ? e.message : fallbackMessage);
      return false;
    } finally {
      setBusy(false);
    }
  }, []);

  // After any review decision, reload everything it can affect (entities, sizing, report,
  // review status, activity feed).
  const refreshAfterReview = async () => {
    await loadDetails("completed");
    await loadCore();
  };

  const onReview = (
    claimId: string,
    action: string,
    override_value?: string,
    notes?: string
  ) =>
    runAction(async () => {
      await api.reviewClaim(id, claimId, action, override_value, notes, reviewerOrUndefined());
      await refreshAfterReview();
    }, "Review failed");

  const onReviewBatch = (reviews: ClaimReviewInput[]) =>
    runAction(async () => {
      await api.reviewClaims(id, reviews, reviewerOrUndefined());
      await refreshAfterReview();
    }, "Batch review failed");

  const onDismissConflict = (conflictId: string, notes?: string) =>
    runAction(async () => {
      await api.dismissConflict(id, conflictId, notes, reviewerOrUndefined());
      await refreshAfterReview();
    }, "Dismiss conflict failed");

  const onReviewEdge = (edgeId: string, action: "accept" | "reject", notes?: string) =>
    runAction(async () => {
      await api.reviewEdge(id, edgeId, action, notes, reviewerOrUndefined());
      await refreshAfterReview();
    }, "Edge review failed");

  const onReviewRecommendation = (
    recommendationId: string,
    action: "accept" | "reject",
    notes?: string
  ) =>
    runAction(async () => {
      await api.reviewRecommendation(id, recommendationId, action, notes, reviewerOrUndefined());
      await refreshAfterReview();
    }, "Recommendation review failed");

  const onRerun = () =>
    runAction(async () => {
      await api.run(id);
      restartPolling();
    }, "Rerun failed");

  const onCompleteReview = () =>
    runAction(async () => {
      setAssessment(await api.completeReview(id));
      await loadDetails("completed"); // sign-off rebuilds the report with polished prose
    }, "Complete review failed");

  const onAddFollowUp = (note: string) => {
    if (!note.trim()) return Promise.resolve(false);
    return runAction(async () => {
      setAssessment(await api.addFollowUp(id, note.trim()));
    }, "Follow-up note failed");
  };

  const onAskQuestion = (question: string) => {
    if (!question.trim()) return Promise.resolve(false);
    return runAction(async () => {
      setAnswers(await api.askQuestion(id, question.trim()));
      try {
        setReport(await api.report(id));
      } catch {
        /* report may not exist yet */
      }
    }, "Ask failed");
  };

  const onRename = (name: string) => {
    const next = name.trim();
    if (!next) return Promise.resolve(false);
    return runAction(async () => {
      setAssessment(await api.updateAssessment(id, next));
    }, "Rename failed");
  };

  const onDeleteAssessment = () =>
    runAction(async () => {
      await api.deleteAssessment(id);
    }, "Delete failed");

  const onAddDocuments = (files: File[]) => {
    if (!files.length) return Promise.resolve(false);
    return runAction(async () => {
      setAssessment(await api.uploadDocuments(id, files));
      restartPolling();
    }, "Upload failed");
  };

  const onRemoveDocument = (documentId: string) =>
    runAction(async () => {
      setAssessment(await api.deleteDocument(id, documentId));
      restartPolling();
    }, "Remove document failed");

  return {
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
    loadState,
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
  };
}
