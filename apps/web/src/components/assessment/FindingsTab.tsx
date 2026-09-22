"use client";

import { useMemo, useState } from "react";
import type { Claim, Conflict, Entity } from "@/lib/api";
import { ConfidenceBadge } from "@/components/StatusBadge";
import { EvidenceTrace } from "@/components/EvidenceTrace";

export function FindingsTab({
  entities,
  claims,
  conflicts,
}: {
  entities: Entity[];
  claims: Claim[];
  conflicts: Conflict[];
}) {
  const [showSuperseded, setShowSuperseded] = useState(false);
  const visibleClaims = showSuperseded ? claims : claims.filter((c) => c.is_selected);
  const grouped = useMemo(() => {
    const byType: Record<string, Entity[]> = {};
    for (const entity of entities) {
      (byType[entity.entity_type] ||= []).push(entity);
    }
    return Object.entries(byType);
  }, [entities]);

  return (
    <div className="space-y-4">
      <div className="card p-5">
        <h2 className="mb-3 text-lg">Inventory entities</h2>
        <div className="space-y-4">
          {grouped.map(([type, rows]) => (
            <div key={type}>
              <h3 className="sans mb-2 text-xs font-semibold uppercase text-[var(--muted)]">
                {type}s
              </h3>
              <div className="sans grid gap-2 md:grid-cols-2">
                {rows.map((e) => (
                  <div key={e.id} className="rounded-lg border border-[var(--border)] p-3">
                    <div className="flex items-center justify-between">
                      <div className="font-semibold">{e.name}</div>
                      <ConfidenceBadge value={e.confidence} />
                    </div>
                    <dl className="mt-2 space-y-1 text-xs">
                      {Object.entries(e.attributes || {}).map(([key, value]) => (
                        <div key={key} className="flex justify-between gap-3">
                          <dt className="text-[var(--muted)]">{key}</dt>
                          <dd>{value}</dd>
                        </div>
                      ))}
                      {!e.attributes || Object.keys(e.attributes).length === 0 ? (
                        <div className="text-[var(--muted)]">No attributes</div>
                      ) : null}
                    </dl>
                  </div>
                ))}
              </div>
            </div>
          ))}
          {!entities.length && (
            <div className="sans text-sm text-[var(--muted)]">No entities yet.</div>
          )}
        </div>
      </div>
      <div className="flex items-center justify-between">
        <h2 className="text-lg">Claims</h2>
        <label className="sans flex items-center gap-2 text-sm">
          <input
            type="checkbox"
            checked={showSuperseded}
            onChange={(e) => setShowSuperseded(e.target.checked)}
          />
          Show superseded
        </label>
      </div>
      <div className="card overflow-x-auto">
        <table className="sans w-full text-left text-sm">
          <thead className="bg-[#faf9f7] text-xs uppercase text-[var(--muted)]">
            <tr>
              <th className="px-3 py-2">Entity</th>
              <th className="px-3 py-2">Attribute</th>
              <th className="px-3 py-2">Value</th>
              <th className="px-3 py-2">Confidence</th>
              <th className="px-3 py-2">Evidence</th>
            </tr>
          </thead>
          <tbody>
            {visibleClaims.map((c) => (
              <tr key={c.id} className="border-t border-[var(--border)] align-top">
                <td className="px-3 py-2">
                  {c.entity_type}:{c.entity_key}
                  {!c.is_selected && (
                    <div className="text-xs text-[var(--danger)]">not selected</div>
                  )}
                </td>
                <td className="px-3 py-2">{c.attribute}</td>
                <td className="px-3 py-2">{c.override_value || c.value}</td>
                <td className="px-3 py-2">
                  <ConfidenceBadge value={c.confidence} />
                </td>
                <td className="px-3 py-2">
                  <EvidenceTrace
                    evidence={c.evidence}
                    fallbackQuote={c.evidence_quote}
                    unsupported={c.unsupported}
                  />
                </td>
              </tr>
            ))}
            {!visibleClaims.length && (
              <tr>
                <td className="px-3 py-6 text-[var(--muted)]" colSpan={5}>
                  No claims to show.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
      {conflicts.length > 0 && (
        <div className="card p-5">
          <h2 className="mb-3 text-lg">Conflicts</h2>
          <ul className="sans space-y-2 text-sm">
            {conflicts.map((c) => (
              <li key={c.id} className="rounded-lg bg-[#fff7ed] px-3 py-2">
                <strong>
                  {c.entity_type}:{c.entity_key}.{c.attribute}
                </strong>{" "}
                — {c.status}
                <div className="text-xs text-[var(--muted)]">{c.resolution_notes}</div>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
