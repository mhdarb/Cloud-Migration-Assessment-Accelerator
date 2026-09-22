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

  if (!graph) {
    return (
      <div className="card p-5 sans text-sm text-[var(--muted)]">
        Dependency graph appears after extraction. Wait for the pipeline to finish, or
        re-run if it already completed.
      </div>
    );
  }

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
      />
      <p className="sans text-xs text-[var(--muted)]">
        Click a node or choose one above to inspect migration blast radius from the
        Neo4j knowledge graph (Postgres fallback if Neo4j is down).
      </p>
    </div>
  );
}
