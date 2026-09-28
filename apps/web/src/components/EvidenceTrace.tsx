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
      {evidence.map((item) => {
        const quotes = item.quotes?.length
          ? item.quotes
          : [item.quote || fallbackQuote].filter((q): q is string => Boolean(q));
        return (
          <div key={item.chunk_id} className="text-xs">
            <div className="font-medium text-[var(--foreground)]">
              {item.filename}
              {item.locator ? ` · ${item.locator}` : ""}
            </div>
            {quotes.map((quote) => (
              <blockquote
                key={quote}
                className="mt-1 border-l-2 border-[var(--border)] pl-2 text-[var(--muted)]"
              >
                “{quote}”
              </blockquote>
            ))}
          </div>
        );
      })}
    </div>
  );
}
