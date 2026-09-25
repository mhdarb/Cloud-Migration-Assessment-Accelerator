/** Compact duration: "45s", "1m 5s", "1h 2m 5s". */
export function formatDuration(totalSeconds: number): string {
  const total = Math.max(0, Math.floor(totalSeconds));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  if (h) return `${h}h ${m}m ${s}s`;
  if (m) return `${m}m ${s}s`;
  return `${s}s`;
}

/** Screen-reader form: "1 minute 5 seconds". */
export function spokenDuration(totalSeconds: number): string {
  const total = Math.max(0, Math.floor(totalSeconds));
  const parts: [number, string][] = [
    [Math.floor(total / 3600), "hour"],
    [Math.floor((total % 3600) / 60), "minute"],
    [total % 60, "second"],
  ];
  const spoken = parts
    .filter(([n], i) => n > 0 || (i === 2 && total === 0))
    .map(([n, unit]) => `${n} ${unit}${n === 1 ? "" : "s"}`);
  return spoken.join(" ");
}
