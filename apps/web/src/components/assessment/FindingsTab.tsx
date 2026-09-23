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
  const [search, setSearch] = useState("");
  const [sortBy, setSortBy] = useState<"entity" | "confidence-desc" | "confidence-asc">("entity");
  const baseClaims = showSuperseded ? claims : claims.filter((c) => c.is_selected);
  const visibleClaims = useMemo(() => {
    const q = search.trim().toLowerCase();
    const filtered = q
      ? baseClaims.filter((c) =>
          [c.entity_type, c.entity_key, c.attribute, c.override_value || c.value]
            .join(" ")
            .toLowerCase()
            .includes(q)
        )
      : baseClaims;
    const sorted = [...filtered];
    if (sortBy === "confidence-desc") sorted.sort((a, b) => b.confidence - a.confidence);
    else if (sortBy === "confidence-asc") sorted.sort((a, b) => a.confidence - b.confidence);
    else sorted.sort((a, b) => `${a.entity_type}:${a.entity_key}`.localeCompare(`${b.entity_type}:${b.entity_key}`));
    return sorted;
  }, [baseClaims, search, sortBy]);
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
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 className="text-lg">Claims{claims.length ? ` (${visibleClaims.length}/${claims.length})` : ""}</h2>
        <div className="sans flex flex-wrap items-center gap-3 text-sm">
          <input
            className="input"
            type="search"
            placeholder="Search entity, attribute, value…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
          <select
            className="input"
            value={sortBy}
            onChange={(e) => setSortBy(e.target.value as typeof sortBy)}
          >
            <option value="entity">Sort: entity</option>
            <option value="confidence-desc">Sort: confidence (high first)</option>
            <option value="confidence-asc">Sort: confidence (low first)</option>
          </select>
          <label className="flex items-center gap-2">
            <input
              type="checkbox"
              checked={showSuperseded}
              onChange={(e) => setShowSuperseded(e.target.checked)}
            />
            Show superseded
          </label>
        </div>
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
                  {search.trim() ? "No claims match your search." : "No claims to show."}
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
