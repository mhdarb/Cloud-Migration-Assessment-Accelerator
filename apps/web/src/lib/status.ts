import { PIPELINE_STAGES } from "./tabs";

/** An assessment in this status won't change again on its own (no more polling needed). */
export const TERMINAL_STATUSES = new Set<string>(["completed", "failed"]);

/** The pipeline is actively running -- destructive actions (delete, etc.) should be
 * disabled while one of these is in progress. Derived from the single canonical
 * `PIPELINE_STAGES` list rather than hand-duplicated. */
export const IN_FLIGHT_STATUSES = new Set<string>(
  PIPELINE_STAGES.filter((s) => s !== "pending" && s !== "completed")
);

export function isTerminal(status: string): boolean {
  return TERMINAL_STATUSES.has(status);
}

export function isInFlight(status: string): boolean {
  return IN_FLIGHT_STATUSES.has(status);
}
