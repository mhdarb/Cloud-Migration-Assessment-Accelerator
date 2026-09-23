import type { Assessment, InfrastructureRecommendation, Report } from "@/lib/api";
import { EvidenceTrace } from "@/components/EvidenceTrace";

function SummaryStat({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="rounded-lg bg-[#faf9f7] px-3 py-2">
      <div className="text-xs uppercase text-[var(--muted)]">{label}</div>
      <div className="text-xl font-semibold break-all">{value}</div>
    </div>
  );
}

export function ReportTab({
  assessment,
  report,
  recommendations,
}: {
  assessment: Assessment;
  report: Report | null;
  recommendations: InfrastructureRecommendation[];
}) {
  if (!report) {
    return (
      <div className="card p-5 sans text-sm text-[var(--muted)]">
        Report not ready yet.
      </div>
    );
  }

  const json = JSON.stringify(report.report_json, null, 2);
  const filename = `${assessment.name.replaceAll(" ", "-")}-report.json`;

  const priced = recommendations.filter(
    (r) => r.result.sku_decision !== "blocked" && r.result.pricing
  );
  const blockedCount = recommendations.length - priced.length;
  const totalMonthly = priced.reduce((sum, r) => sum + (r.result.pricing?.monthly_total || 0), 0);
  const currency = priced[0]?.result.pricing?.currency || "USD";
  const m = report.metrics || {};

  return (
    <div className="space-y-4">
      <div className="card p-5">
        <h2 className="mb-3 text-lg">Migration summary</h2>
        <div className="sans grid grid-cols-2 gap-3 text-sm md:grid-cols-4">
          <SummaryStat
            label="Est. monthly cost"
            value={recommendations.length ? `${currency} ${totalMonthly.toFixed(2)}` : "—"}
          />
          <SummaryStat label="Applications" value={m.application_count ?? 0} />
          <SummaryStat label="Servers" value={m.server_count ?? 0} />
          <SummaryStat label="Databases" value={m.database_count ?? 0} />
          <SummaryStat label="Dependencies" value={m.edge_count ?? 0} />
          <SummaryStat label="Cited claims" value={`${m.cited_claim_pct ?? 0}%`} />
          <SummaryStat label="Open conflicts" value={m.conflict_count ?? 0} />
          <SummaryStat label="Review queue" value={m.review_queue_count ?? 0} />
        </div>
        {blockedCount > 0 && (
          <p className="sans mt-3 text-xs text-[var(--danger)]">
            {blockedCount} server{blockedCount === 1 ? "" : "s"} blocked pending review —
            excluded from the cost total. See Sizing.
          </p>
        )}
      </div>
      <div className="card p-5">
        <h2 className="mb-2 text-lg">Readiness summary</h2>
        <p className="sans text-sm leading-relaxed">{report.readiness_summary}</p>
      </div>
      <div className="grid gap-4 md:grid-cols-2">
        <div className="card p-5">
          <h2 className="mb-2 text-lg">Gaps</h2>
          <ul className="sans list-disc space-y-1 pl-5 text-sm">
            {report.gaps.map((g, i) => (
              <li key={i}>{g}</li>
            ))}
            {!report.gaps.length && (
              <li className="list-none text-[var(--muted)]">No gaps recorded.</li>
            )}
          </ul>
        </div>
        <div className="card p-5">
          <h2 className="mb-2 text-lg">Assumptions</h2>
          <ul className="sans list-disc space-y-1 pl-5 text-sm">
            {report.assumptions.map((g, i) => (
              <li key={i}>{g}</li>
            ))}
          </ul>
        </div>
      </div>
      <div className="card p-5">
        <h2 className="mb-3 text-lg">Evidence appendix</h2>
        <div className="space-y-4">
          {report.evidence_appendix.map((item) => (
            <div
              key={item.claim_id}
              className="border-b border-[var(--border)] pb-4 last:border-0 last:pb-0"
            >
              <div className="sans text-sm font-medium">
                {item.entity}.{item.attribute} = {item.value}
              </div>
              <div className="sans mt-2">
                <EvidenceTrace
                  evidence={item.evidence}
                  fallbackQuote={item.quote}
                  unsupported={item.unsupported}
                />
              </div>
            </div>
          ))}
          {!report.evidence_appendix.length && (
            <div className="sans text-sm text-[var(--muted)]">
              No cited claims are available.
            </div>
          )}
        </div>
      </div>
      <div className="card p-5">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h2 className="text-lg">Export JSON</h2>
            <p className="sans mt-1 text-sm text-[var(--muted)]">
              Full inventory, questions, sizing, and evidence for downstream tools.
            </p>
          </div>
          <a
            className="btn btn-secondary"
            href={`data:application/json,${encodeURIComponent(json)}`}
            download={filename}
          >
            Download
          </a>
        </div>
      </div>
    </div>
  );
}
