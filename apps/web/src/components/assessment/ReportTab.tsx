import type { Assessment, Report } from "@/lib/api";
import { EvidenceTrace } from "@/components/EvidenceTrace";

export function ReportTab({
  assessment,
  report,
}: {
  assessment: Assessment;
  report: Report | null;
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

  return (
    <div className="space-y-4">
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
