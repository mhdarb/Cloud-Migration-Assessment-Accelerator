export const TABS = [
  "overview",
  "questions",
  "sizing",
  "findings",
  "graph",
  "report",
  "review",
] as const;

export type Tab = (typeof TABS)[number];

export function parseTab(raw: string | null): Tab {
  return TABS.includes(raw as Tab) ? (raw as Tab) : "overview";
}

export const PIPELINE_STAGES = [
  "pending",
  "ingesting",
  "extracting",
  "reconciling",
  "building_graph",
  "generating_report",
  "completed",
] as const;
