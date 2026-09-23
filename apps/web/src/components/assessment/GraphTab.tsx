"use client";

import { useState } from "react";
import { api, type GraphOut } from "@/lib/api";
import { DependencyGraph } from "@/components/DependencyGraph";

export function GraphTab({
  id,
  graph,
  onError,
}: {
  id: string;
  graph: GraphOut | null;
  onError: (message: string) => void;
}) {
  const [blastCenter, setBlastCenter] = useState("");
  const [blastDepth, setBlastDepth] = useState(2);
  const [blastIds, setBlastIds] = useState<Set<string> | undefined>(undefined);
  const [blastSource, setBlastSource] = useState("");
  const [blastBusy, setBlastBusy] = useState(false);
  const [selectedEdge, setSelectedEdge] = useState<GraphOut["edges"][number] | null>(null);

  if (!graph) {
    return (
      <div className="card p-5 sans text-sm text-[var(--muted)]">
        Dependency graph appears after extraction. Wait for the pipeline to finish, or
        re-run if it already completed.
      </div>
    );
  }

  const nodeLabelById = new Map(graph.nodes.map((n) => [n.id, n.label]));

  return (
    <div className="space-y-4">
      <div className="card flex flex-wrap items-end gap-3 p-4 sans text-sm">
        <div className="min-w-[240px] flex-1">
          <label className="mb-1 block text-xs uppercase text-[var(--muted)]">
            Blast radius center
          </label>
          <select
            className="input"
            value={blastCenter}
            onChange={(e) => setBlastCenter(e.target.value)}
          >
            <option value="">Select a node…</option>
            {graph.nodes.map((n) => (
              <option key={n.id} value={n.id}>
                {n.label} ({n.type})
              </option>
            ))}
          </select>
        </div>
        <div>
          <label className="mb-1 block text-xs uppercase text-[var(--muted)]">Depth</label>
          <select
            className="input"
            value={blastDepth}
            onChange={(e) => setBlastDepth(Number(e.target.value))}
          >
            {[1, 2, 3].map((d) => (
              <option key={d} value={d}>
                {d}
              </option>
            ))}
          </select>
        </div>
        <button
          className="btn btn-primary"
          type="button"
          disabled={!blastCenter || blastBusy}
          onClick={async () => {
            if (!blastCenter) return;
            setBlastBusy(true);
            try {
              const br = await api.blastRadius(id, blastCenter, blastDepth);
              setBlastIds(new Set(br.nodes.map((n) => n.id)));
              setBlastSource(br.source);
            } catch (e) {
              onError(e instanceof Error ? e.message : "Blast radius failed");
            } finally {
              setBlastBusy(false);
            }
          }}
        >
          Show blast radius
        </button>
        <button
          className="btn btn-secondary"
          type="button"
          onClick={() => {
            setBlastIds(undefined);
            setBlastSource("");
          }}
        >
          Clear
        </button>
        {blastSource && (
          <div className="text-xs text-[var(--muted)]">
            Source: {blastSource}
            {blastIds ? ` · ${blastIds.size} nodes` : ""}
          </div>
        )}
      </div>
      <DependencyGraph
        data={graph}
        highlightIds={blastIds}
        centerId={blastCenter || null}
        onNodeSelect={(nodeId) => setBlastCenter(nodeId)}
        onEdgeSelect={(edge) => setSelectedEdge(edge)}
      />
      {selectedEdge && (
        <div className="card p-4 sans text-sm">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <div className="font-semibold">
                {nodeLabelById.get(selectedEdge.source) || selectedEdge.source}
                {" → "}
                {nodeLabelById.get(selectedEdge.target) || selectedEdge.target}
              </div>
              <div className="mt-1 text-xs text-[var(--muted)]">
                {selectedEdge.relationship.replaceAll("_", " ")} ·{" "}
                {Math.round(selectedEdge.confidence * 100)}% confidence
                {selectedEdge.needs_human_review ? " · needs review" : ""}
              </div>
            </div>
            <button
              type="button"
              className="btn btn-secondary"
              onClick={() => setSelectedEdge(null)}
            >
              Close
            </button>
          </div>
          {selectedEdge.evidence_quote ? (
            <blockquote className="mt-3 border-l-2 border-[var(--border)] pl-2 text-[var(--muted)]">
              “{selectedEdge.evidence_quote}”
            </blockquote>
          ) : selectedEdge.rationale ? (
            <p className="mt-3 text-xs text-[var(--muted)]">
              Inferred, not extracted: {selectedEdge.rationale}
            </p>
          ) : (
            <p className="mt-3 text-xs text-[var(--muted)]">No citation recorded.</p>
          )}
        </div>
      )}
      <p className="sans text-xs text-[var(--muted)]">
        Click a node or choose one above to inspect migration blast radius; click an edge
        to see why it was extracted. Border weight reflects each component&apos;s
        centrality — how much of the estate touches it — and dashed edges are inferred
        relationships flagged for review, not extracted facts.
      </p>
    </div>
  );
}
