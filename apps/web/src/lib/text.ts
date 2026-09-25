/**
 * Display helpers for model-written and machine-named text.
 *
 * The API now strips Markdown from AI-written prose before saving it, but explanations
 * and answers saved before that change still contain it ("### Recommendation",
 * "**Standard_D4s_v5**"). `plainText` cleans those at render time, so they read properly
 * without re-running the pipeline. Render the result with `whitespace-pre-line` so its
 * paragraph and list line breaks survive.
 */
export function plainText(text: string | null | undefined): string {
  if (!text) return "";
  return text
    .replace(/```[^\n]*\n?([\s\S]*?)```/g, "$1")
    .replace(/^[ \t]*\|?[ \t]*:?-{3,}:?[ \t]*(\|[ \t]*:?-{3,}:?[ \t]*)*\|?[ \t]*(\n|$)/gm, "")
    .replace(/^[ \t]*\|(.+)\|[ \t]*$/gm, (_m, cells: string) =>
      cells
        .split("|")
        .map((c) => c.trim())
        .filter(Boolean)
        .join(" · ")
    )
    .replace(/^[ \t]*(?:-{3,}|\*{3,}|_{3,})[ \t]*$/gm, "")
    .replace(/^[ \t]{0,3}#{1,6}[ \t]*/gm, "")
    .replace(/^[ \t]*(?:[-*+•]|\d+[.)])[ \t]+/gm, "• ")
    .replace(/\[([^\]]+)\]\([^)]+\)/g, "$1")
    .replace(/`([^`]+)`/g, "$1")
    .replace(/(\*\*|__)(.+?)\1/g, "$2")
    .replace(/(^|[^\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])/g, "$1$2")
    .split("\n")
    .map((line) => line.replace(/\s+/g, " ").trim())
    .join("\n")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

/** Internal sizing field names as people say them. */
const FIELD_LABELS: Record<string, string> = {
  vcpus: "vCPU count",
  memory_gb: "memory",
  cpu_utilization_pct: "CPU utilization",
  memory_utilization_pct: "memory utilization",
  disk_gb: "disk size",
  disk_iops: "disk IOPS",
  disk_throughput_mbps: "disk throughput",
  os: "operating system",
  operating_system: "operating system",
  architecture: "architecture",
  environment: "environment",
  disk_performance: "disk performance",
  capacity: "capacity",
};

export function fieldLabel(name: string): string {
  return FIELD_LABELS[name] ?? name.replace(/_/g, " ");
}

export function fieldList(names: string[] | undefined): string {
  return (names ?? []).map(fieldLabel).join(", ");
}

/** Who wrote a sizing explanation, in words a reviewer understands. */
export function explanationSourceLabel(source: string | undefined): string {
  if (!source) return "";
  if (source === "deterministic-template") return "rule-based explanation";
  if (source === "deterministic-template-fallback") return "rule-based explanation (AI unavailable)";
  return "AI-written explanation";
}

/** 500 -> "500", 2.5 -> "2.5", and "—" for anything missing. */
export function num(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return Number.isInteger(value) ? String(value) : String(Math.round(value * 100) / 100);
}
