import type { InfrastructureRecommendation } from "@/lib/api";

function skuName(item: Record<string, unknown>): string {
  if (typeof item.name === "string") return item.name;
  if (typeof item.sku === "string") return item.sku;
  if (typeof item.recommended_sku === "string") return item.recommended_sku;
  return "SKU";
}

export function SizingTab({
  recommendations,
  loadState,
}: {
  recommendations: InfrastructureRecommendation[];
  /** undefined while the first load is in flight. */
  loadState?: "loaded" | "failed";
}) {
  const priced = recommendations.filter(
    (r) => r.result.sku_decision !== "blocked" && r.result.pricing
  );
  const blockedCount = recommendations.length - priced.length;
  const totalMonthly = priced.reduce((sum, r) => sum + (r.result.pricing?.monthly_total || 0), 0);
  const currency = priced[0]?.result.pricing?.currency || "USD";

  return (
    <div className="space-y-4">
      <div>
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h2 className="text-xl">Azure sizing &amp; cost recommendations</h2>
            <p className="sans mt-1 text-sm text-[var(--muted)]">
              Deterministic rules choose VM and disk options; AI may explain but never
              changes the sizing decision.
            </p>
          </div>
          {recommendations.length > 0 && (
            <div className="text-right">
              <div className="text-2xl font-semibold text-[var(--accent)]">
                {currency} {totalMonthly.toFixed(2)}/mo
              </div>
              <div className="sans text-xs text-[var(--muted)]">
                Across {priced.length} server{priced.length === 1 ? "" : "s"}
                {blockedCount > 0
                  ? ` · ${blockedCount} blocked (excluded)`
                  : ""}
              </div>
            </div>
          )}
        </div>
      </div>
      {recommendations.map((recommendation) => {
        const result = recommendation.result;
        const blocked = result.sku_decision === "blocked" || !result.recommended_sku;
        const diskName =
          result.disk && typeof result.disk.name === "string" ? result.disk.name : "";
        const checks = result.compatibility_checks || [];
        const alternatives = result.alternatives || [];
        const needsReview =
          recommendation.needs_human_review || result.needs_human_review;
        return (
          <div className="card p-5" key={recommendation.id}>
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div>
                <div className="flex flex-wrap items-center gap-2">
                  <h3 className="text-lg">{result.server}</h3>
                  {needsReview && (
                    <span className="badge bg-amber-100 text-amber-800">Needs review</span>
                  )}
                </div>
                <div className="sans text-sm font-semibold text-[var(--accent)]">
                  {blocked
                    ? "No Azure IaaS SKU recommended"
                    : `${result.recommended_sku}${diskName ? ` + ${diskName}` : ""}`}
                </div>
              </div>
              <div className="text-right">
                <div className="text-xl font-semibold">
                  {blocked
                    ? "—"
                    : `${result.pricing.currency} ${result.pricing.monthly_total}/mo`}
                </div>
                <div className="sans text-xs text-[var(--muted)]">
                  {blocked
                    ? "Blocked pending review"
                    : `Estimate · ${result.pricing.source} · catalog ${result.catalog_version || ""}`}
                </div>
              </div>
            </div>
            <p className="sans mt-3 text-sm">{result.explanation}</p>
            {result.measured_fields && result.measured_fields.length > 0 && (
              <p className="sans mt-2 text-xs text-[var(--muted)]">
                Measured: {result.measured_fields.join(", ")}
              </p>
            )}
            {result.assumed_fields && result.assumed_fields.length > 0 && (
              <p className="sans mt-1 text-xs text-amber-800">
                Assumed (not measured): {result.assumed_fields.join(", ")}
              </p>
            )}
            <div className="sans mt-4 grid gap-3 text-sm md:grid-cols-3">
              <div className="rounded-lg bg-[#faf9f7] p-3">
                Required: {result.required?.vcpus ?? "—"} vCPU /{" "}
                {result.required?.memory_gb ?? "—"} GB RAM
              </div>
              <div className="rounded-lg bg-[#faf9f7] p-3">
                Disk: {result.required?.disk_gb ?? "—"} GB / {result.required?.disk_iops ?? "—"} IOPS
              </div>
              <div className="rounded-lg bg-[#faf9f7] p-3">
                Confidence: {Math.round(result.confidence * 100)}%
              </div>
            </div>
            {result.assumptions && result.assumptions.length > 0 && (
              <div className="sans mt-4 rounded-lg bg-amber-50 p-3 text-sm text-amber-800">
                <div className="font-semibold">Assumptions requiring review</div>
                <ul className="mt-1 list-disc pl-5">
                  {result.assumptions.map((assumption) => (
                    <li key={assumption}>{assumption}</li>
                  ))}
                </ul>
              </div>
            )}
            {checks.length > 0 && (
              <details className="sans mt-4 text-sm">
                <summary className="cursor-pointer font-semibold">
                  Compatibility checks ({checks.length})
                </summary>
                <ul className="mt-2 space-y-1">
                  {checks.map((check) => (
                    <li key={`${check.check}-${check.detail}`}>
                      <span
                        className={
                          check.status === "fail" || check.status === "failed"
                            ? "text-[var(--danger)]"
                            : "text-[var(--success)]"
                        }
                      >
                        {check.status}
                      </span>
                      {` · ${check.check}: ${check.detail}`}
                    </li>
                  ))}
                </ul>
              </details>
            )}
            {alternatives.length > 0 && (
              <details className="sans mt-3 text-sm">
                <summary className="cursor-pointer font-semibold">
                  Alternatives evaluated ({alternatives.length})
                </summary>
                <ul className="mt-2 list-disc pl-5">
                  {alternatives.map((alt, i) => {
                    const price =
                      alt.price && typeof alt.price === "object"
                        ? (alt.price as { monthly?: number }).monthly
                        : undefined;
                    return (
                      <li key={`${skuName(alt)}-${i}`}>
                        {skuName(alt)}
                        {typeof alt.vcpus === "number" ? ` · ${alt.vcpus} vCPU` : ""}
                        {typeof alt.memory_gb === "number" ? ` / ${alt.memory_gb} GB` : ""}
                        {typeof price === "number" ? ` · ~${price}/mo` : ""}
                      </li>
                    );
                  })}
                </ul>
              </details>
            )}
            <div className="sans mt-4 text-xs text-[var(--muted)]">
              Region {recommendation.region} · catalog {result.catalog_version || "n/a"} ·
              explanation {result.explanation_source}
            </div>
          </div>
        );
      })}
      {recommendations.length === 0 && (
        <div className="card p-5 sans text-sm text-[var(--muted)]">
          {loadState === "failed"
            ? "Sizing couldn’t be loaded. Refresh the page to try again."
            : !loadState
              ? "Loading sizing…"
              : "Recommendations appear after the pipeline identifies server inventory."}
        </div>
      )}
    </div>
  );
}
