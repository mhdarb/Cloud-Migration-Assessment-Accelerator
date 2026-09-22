const COLORS: Record<string, { bg: string; fg: string }> = {
  pending: { bg: "#f3f4f6", fg: "#374151" },
  ingesting: { bg: "#dbeafe", fg: "#1d4ed8" },
  extracting: { bg: "#e0e7ff", fg: "#4338ca" },
  reconciling: { bg: "#fef3c7", fg: "#b45309" },
  building_graph: { bg: "#ede9fe", fg: "#6d28d9" },
  generating_report: { bg: "#cffafe", fg: "#0e7490" },
  completed: { bg: "#dcfce7", fg: "#15803d" },
  failed: { bg: "#fee2e2", fg: "#b91c1c" },
};

export function StatusBadge({ status }: { status: string }) {
  const c = COLORS[status] || COLORS.pending;
  return (
    <span className="badge" style={{ background: c.bg, color: c.fg }}>
      {status.replaceAll("_", " ")}
    </span>
  );
}

export function ConfidenceBadge({ value }: { value: number }) {
  const pct = Math.round(value * 100);
  const color =
    value >= 0.8 ? "var(--success)" : value >= 0.7 ? "var(--warn)" : "var(--danger)";
  return (
    <span className="badge" style={{ background: "#f3f4f6", color }}>
      {pct}%
    </span>
  );
}
