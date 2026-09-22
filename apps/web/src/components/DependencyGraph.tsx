"use client";

import { useMemo } from "react";
import {
  Background,
  Controls,
  MarkerType,
  MiniMap,
  ReactFlow,
  type Edge,
  type Node,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import type { GraphOut } from "@/lib/api";

const TYPE_COLOR: Record<string, string> = {
  application: "#d04a02",
  server: "#1d4ed8",
  database: "#0f7b4c",
  interface: "#6d28d9",
};

export function DependencyGraph({
  data,
  highlightIds,
  centerId,
  onNodeSelect,
}: {
  data: GraphOut;
  highlightIds?: Set<string>;
  centerId?: string | null;
  onNodeSelect?: (nodeId: string) => void;
}) {
  const { nodes, edges } = useMemo(() => {
    const byType: Record<string, number> = {};
    const highlight = highlightIds;
    const nodes: Node[] = data.nodes.map((n) => {
      const col = byType[n.type] ?? 0;
      byType[n.type] = col + 1;
      const row = ["application", "server", "database", "interface"].indexOf(n.type);
      const isCenter = centerId === n.id;
      const inBlast = !highlight || highlight.has(n.id);
      const dimmed = Boolean(highlight && highlight.size > 0 && !inBlast);
      return {
        id: n.id,
        position: { x: 80 + col * 220, y: 40 + Math.max(row, 0) * 140 },
        data: { label: `${n.label}\n(${Math.round(n.confidence * 100)}%)` },
        style: {
          border: `2px solid ${
            isCenter ? "#111827" : TYPE_COLOR[n.type] || "#666"
          }`,
          borderRadius: 10,
          padding: 10,
          background: isCenter ? "#fdebe3" : "#fff",
          fontSize: 12,
          whiteSpace: "pre-line",
          width: 160,
          textAlign: "center",
          fontFamily: "ui-sans-serif, system-ui, sans-serif",
          opacity: dimmed ? 0.25 : 1,
          boxShadow: isCenter ? "0 0 0 2px #d04a02" : undefined,
        },
      };
    });

    const edges: Edge[] = data.edges.map((e) => {
      const inBlast =
        !highlight ||
        highlight.size === 0 ||
        (highlight.has(e.source) && highlight.has(e.target));
      return {
        id: e.id,
        source: e.source,
        target: e.target,
        label: e.relationship,
        animated: e.needs_human_review || Boolean(centerId && inBlast),
        style: {
          stroke: !inBlast
            ? "#e5e2dc"
            : e.needs_human_review
              ? "#b45309"
              : "#9ca3af",
          strokeWidth: inBlast && centerId ? 2.5 : e.needs_human_review ? 2 : 1.5,
          opacity: inBlast ? 1 : 0.2,
        },
        markerEnd: { type: MarkerType.ArrowClosed, color: "#9ca3af" },
        labelStyle: { fontSize: 10, fill: "#6b7280" },
      };
    });

    return { nodes, edges };
  }, [data, highlightIds, centerId]);

  if (!data.nodes.length) {
    return (
      <div className="sans rounded-lg border border-dashed border-[var(--border)] p-8 text-center text-sm text-[var(--muted)]">
        No graph nodes yet. Wait for the pipeline to finish extraction.
      </div>
    );
  }

  return (
    <div className="h-[520px] overflow-hidden rounded-xl border border-[var(--border)] bg-white">
      <ReactFlow
        nodes={nodes}
        edges={edges}
        fitView
        proOptions={{ hideAttribution: true }}
        onNodeClick={(_, node) => onNodeSelect?.(node.id)}
      >
        <Background gap={18} size={1} color="#e5e2dc" />
        <MiniMap pannable zoomable />
        <Controls />
      </ReactFlow>
    </div>
  );
}
