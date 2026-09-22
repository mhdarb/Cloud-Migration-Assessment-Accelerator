import type { Evidence } from "@/lib/api";

export function EvidenceTrace({
  evidence,
  fallbackQuote,
  unsupported = false,
}: {
  evidence: Evidence[] | undefined;
  fallbackQuote?: string | null;
  unsupported?: boolean;
}) {
  if (unsupported || !evidence?.length) {
    return (
      <div className="text-xs text-[var(--danger)]">
        No source document{fallbackQuote ? ` · ${fallbackQuote}` : ""}
      </div>
    );
  }
  return (
    <div className="space-y-2">
      {evidence.map((item) => (
        <div key={item.chunk_id} className="text-xs">
          <div className="font-medium text-[var(--foreground)]">
            {item.filename}
            {item.page ? ` · p. ${item.page}` : ""}
          </div>
          {(item.quote || fallbackQuote) && (
            <blockquote className="mt-1 border-l-2 border-[var(--border)] pl-2 text-[var(--muted)]">
              “{item.quote || fallbackQuote}”
            </blockquote>
          )}
        </div>
      ))}
    </div>
  );
}
