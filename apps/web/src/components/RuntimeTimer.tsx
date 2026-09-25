"use client";

import { useEffect, useState } from "react";
import { formatDuration, spokenDuration } from "@/lib/duration";

/**
 * Pipeline runtime in seconds.
 *
 * Anchored on the server-computed `runtimeSeconds` (re-anchored whenever a poll brings a
 * new value) and ticked forward locally once a second in between, so it counts smoothly
 * without a poll per second — and without subtracting the browser clock from a server
 * timestamp, which would drift with clock skew. While running it never steps backwards: a
 * freshly polled value is computed before the network round trip, so it can land a little
 * behind the local count. Once the run ends it shows the server's final value exactly.
 *
 * Render it with `key={pipeline_started_at}` so a re-run remounts it and counts from 0.
 */
export function RuntimeTimer({
  runtimeSeconds,
  status,
  running,
  compact = false,
}: {
  runtimeSeconds: number | null | undefined;
  status: string;
  running: boolean;
  /** Table-cell form: just the number (the full label stays in aria-label/title). */
  compact?: boolean;
}) {
  const [seconds, setSeconds] = useState<number | null>(
    runtimeSeconds == null ? null : Math.floor(runtimeSeconds)
  );

  useEffect(() => {
    if (runtimeSeconds == null) {
      setSeconds(null);
      return;
    }
    if (!running) {
      setSeconds(Math.floor(runtimeSeconds)); // final, authoritative value
      return;
    }
    const anchoredAt = performance.now();
    const tick = () => {
      const now = Math.floor(runtimeSeconds + (performance.now() - anchoredAt) / 1000);
      setSeconds((prev) => Math.max(prev ?? 0, now));
    };
    tick();
    const id = setInterval(tick, 1000);
    return () => clearInterval(id);
  }, [runtimeSeconds, running]);

  if (seconds == null) return null;

  const label = running ? "Running" : status === "failed" ? "Failed after" : "Completed in";

  return (
    <span
      role="timer"
      aria-label={`${label} ${spokenDuration(seconds)}`}
      title={`${label} ${formatDuration(seconds)} (${seconds} seconds)`}
      className="sans inline-flex items-center gap-1 text-sm text-[var(--muted)]"
    >
      <span aria-hidden="true">⏱</span>
      {compact ? null : <>{label} </>}
      <span className="font-medium tabular-nums text-[var(--foreground)]">
        {formatDuration(seconds)}
      </span>
    </span>
  );
}
