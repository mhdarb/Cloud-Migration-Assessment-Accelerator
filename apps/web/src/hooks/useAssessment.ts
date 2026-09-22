"use client";

import { useCallback, useEffect, useState } from "react";
import {
  api,
  Assessment,
  AssessmentAnswers,
  Claim,
  Conflict,
  Entity,
  GraphOut,
  InfrastructureRecommendation,
  Report,
} from "@/lib/api";

const TERMINAL = new Set(["completed", "failed"]);

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
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [pollGeneration, setPollGeneration] = useState(0);

  const loadCore = useCallback(async () => {
    const a = await api.getAssessment(id);
    setAssessment(a);
    return a;
  }, [id]);

  const loadDetails = useCallback(async (status: string) => {
    const settled = await Promise.allSettled([
      api.claims(id),
      api.entities(id),
      api.graph(id),
      api.conflicts(id),
      api.assessmentQuestions(id),
      api.recommendations(id),
    ]);
    const failed: string[] = [];
    const assign = <T,>(
      result: PromiseSettledResult<T>,
      label: string,
      setter: (value: T) => void
    ) => {
      if (result.status === "fulfilled") setter(result.value);
      else failed.push(label);
    };
    assign(settled[0], "claims", setClaims);
    assign(settled[1], "entities", setEntities);
    assign(settled[2], "graph", setGraph);
    assign(settled[3], "conflicts", setConflicts);
    assign(settled[4], "questions", setAnswers);
    assign(settled[5], "recommendations", setRecommendations);

    try {
      setReport(await api.report(id));
    } catch {
      setReport(null);
      if (TERMINAL.has(status)) failed.push("report");
    }

    if (failed.length && TERMINAL.has(status)) {
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
        if (TERMINAL.has(a.status)) return;
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

  const onReview = async (
    claimId: string,
    action: string,
    override_value?: string,
    notes?: string
  ) => {
    setBusy(true);
    setError(null);
    try {
      await api.reviewClaim(id, claimId, action, override_value, notes);
      await loadDetails("completed");
      await loadCore();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Review failed");
    } finally {
      setBusy(false);
    }
  };

  const onRerun = async () => {
    setBusy(true);
    setError(null);
    try {
      await api.run(id);
      restartPolling();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Rerun failed");
    } finally {
      setBusy(false);
    }
  };

  const onCompleteReview = async () => {
    setBusy(true);
    setError(null);
    try {
      setAssessment(await api.completeReview(id));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Complete review failed");
    } finally {
      setBusy(false);
    }
  };

  const onAddFollowUp = async (note: string) => {
    if (!note.trim()) return false;
    setBusy(true);
    setError(null);
    try {
      setAssessment(await api.addFollowUp(id, note.trim()));
      return true;
    } catch (e) {
      setError(e instanceof Error ? e.message : "Follow-up note failed");
      return false;
    } finally {
      setBusy(false);
    }
  };

  const onAskQuestion = async (question: string) => {
    if (!question.trim()) return false;
    setBusy(true);
    setError(null);
    try {
      setAnswers(await api.askQuestion(id, question.trim()));
      try {
        setReport(await api.report(id));
      } catch {
        /* report may not exist yet */
      }
      return true;
    } catch (e) {
      setError(e instanceof Error ? e.message : "Ask failed");
      return false;
    } finally {
      setBusy(false);
    }
  };

  const onRename = async (name: string) => {
    const next = name.trim();
    if (!next) return false;
    setBusy(true);
    setError(null);
    try {
      setAssessment(await api.updateAssessment(id, next));
      return true;
    } catch (e) {
      setError(e instanceof Error ? e.message : "Rename failed");
      return false;
    } finally {
      setBusy(false);
    }
  };

  const onDeleteAssessment = async () => {
    setBusy(true);
    setError(null);
    try {
      await api.deleteAssessment(id);
      return true;
    } catch (e) {
      setError(e instanceof Error ? e.message : "Delete failed");
      return false;
    } finally {
      setBusy(false);
    }
  };

  const onAddDocuments = async (files: File[]) => {
    if (!files.length) return;
    setBusy(true);
    setError(null);
    try {
      setAssessment(await api.uploadDocuments(id, files));
      restartPolling();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Upload failed");
    } finally {
      setBusy(false);
    }
  };

  const onRemoveDocument = async (documentId: string) => {
    setBusy(true);
    setError(null);
    try {
      setAssessment(await api.deleteDocument(id, documentId));
      restartPolling();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Remove document failed");
    } finally {
      setBusy(false);
    }
  };

  return {
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
  };
}
