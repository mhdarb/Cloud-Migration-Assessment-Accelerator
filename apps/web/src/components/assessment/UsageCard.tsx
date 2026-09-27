"use client";

import type { Usage, UsageTotals } from "@/lib/api";

function money(value: number, currency: string): string {
  const digits = value > 0 && value < 0.01 ? 4 : 2;
  try {
    return new Intl.NumberFormat(undefined, {
      style: "currency",
      currency,
      minimumFractionDigits: digits,
      maximumFractionDigits: digits,
    }).format(value);
  } catch {
    return `${currency} ${value.toFixed(digits)}`;
  }
}

const n = (value: number) => value.toLocaleString();

function Tile({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="rounded-lg bg-[#faf9f7] px-3 py-2">
      <div className="text-xs uppercase text-[var(--muted)]">{label}</div>
      <div className="text-xl font-semibold tabular-nums">{value}</div>
      {sub && <div className="text-xs text-[var(--muted)] tabular-nums">{sub}</div>}
    </div>
  );
}

function when(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso.endsWith("Z") || iso.includes("+") ? iso : `${iso}Z`);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString();
}

const plural = (count: number, word: string) => `${n(count)} ${word}${count === 1 ? "" : "s"}`;

function callsLine(t: UsageTotals): string {
  return `${plural(t.chat.calls, "LLM call")} · ${plural(t.embeddings.calls, "embedding call")}`;
}

/**
 * Model usage and estimated cost for the latest pipeline run (by stage), for everything
 * done since it (review clicks, questions, questionnaires), and for earlier runs.
 */
export function UsageCard({ usage, running }: { usage: Usage | null; running: boolean }) {
  if (!usage) {
    return (
      <div className="card p-5 md:col-span-2">
        <h2 className="text-lg">AI usage &amp; cost</h2>
        <p className="sans mt-2 text-sm text-[var(--muted)]">Loading usage…</p>
      </div>
    );
  }
  const run = usage.latest_run;
  const cur = usage.currency;
  const noCalls = run.chat.calls === 0 && run.embeddings.calls === 0;
  const since = usage.since_run;
  const earlier = usage.runs.filter((r) => r.run_started_at !== run.run_started_at);

  return (
    <div className="card p-5 md:col-span-2">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="text-lg">AI usage &amp; cost</h2>
        <span className="sans text-xs text-[var(--muted)]">
          {running ? "Current run so far" : "Latest run"}
          {run.run_started_at ? ` · started ${when(run.run_started_at)}` : ""}
        </span>
      </div>

      <div className="sans mt-3 grid grid-cols-1 gap-3 text-sm sm:grid-cols-3">
        <Tile
          label="LLM calls"
          value={n(run.chat.calls)}
          sub={`${n(run.chat.input_tokens)} in · ${n(run.chat.output_tokens)} out tokens`}
        />
        <Tile
          label="Embedding calls"
          value={n(run.embeddings.calls)}
          sub={run.embeddings.tokens ? `${n(run.embeddings.tokens)} tokens` : "local model — no token charge"}
        />
        <Tile
          label="Estimated cost"
          value={money(run.total_cost, cur)}
          sub={`LLM ${money(run.chat.cost, cur)} · embeddings ${money(run.embeddings.cost, cur)}`}
        />
      </div>

      {noCalls ? (
        <p className="sans mt-3 text-sm text-[var(--muted)]">
          {running
            ? "No model calls recorded yet for this run."
            : "No model calls were recorded for this run (offline/mock mode, or a run from before usage tracking)."}
        </p>
      ) : (
        run.by_stage.length > 0 && (
          <div className="sans mt-4 overflow-x-auto">
            <table className="w-full text-sm tabular-nums">
              <thead className="text-left text-xs text-[var(--muted)]">
                <tr>
                  <th className="py-1 pr-3 font-medium">Stage</th>
                  <th className="py-1 pr-3 text-right font-medium">LLM calls</th>
                  <th className="py-1 pr-3 text-right font-medium">Tokens in / out</th>
                  <th className="py-1 pr-3 text-right font-medium">Embedding calls</th>
                  <th className="py-1 text-right font-medium">Cost</th>
                </tr>
              </thead>
              <tbody>
                {run.by_stage.map((s) => (
                  <tr key={s.stage} className="border-t border-[#eee]">
                    <td className="py-1 pr-3">{s.label}</td>
                    <td className="py-1 pr-3 text-right">{n(s.chat.calls)}</td>
                    <td className="py-1 pr-3 text-right">
                      {n(s.chat.input_tokens)} / {n(s.chat.output_tokens)}
                    </td>
                    <td className="py-1 pr-3 text-right">{n(s.embeddings.calls)}</td>
                    <td className="py-1 text-right">{money(s.total_cost, cur)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )
      )}

      <div className="sans mt-4 grid gap-1 text-sm">
        <div>
          <span className="font-medium">Since this run</span>{" "}
          <span className="text-[var(--muted)]">(reviews, questions, questionnaires):</span>{" "}
          {callsLine(since)} · {money(since.total_cost, cur)}
        </div>
        {earlier.length > 0 && (
          <details>
            <summary className="cursor-pointer font-medium">Earlier runs ({earlier.length})</summary>
            <ul className="mt-1 space-y-0.5 text-[var(--muted)]">
              {earlier.map((r) => (
                <li key={r.run_started_at} className="tabular-nums">
                  {when(r.run_started_at)} — {callsLine(r)} · {money(r.total_cost, cur)}
                </li>
              ))}
            </ul>
          </details>
        )}
        <div className="font-medium">
          All runs and activity: {money(usage.all_time.total_cost, cur)}
        </div>
      </div>

      <p className="sans mt-3 text-xs text-[var(--muted)]">
        Estimates at {money(usage.rates.llm_input_per_million, cur)} / {money(usage.rates.llm_output_per_million, cur)} per
        1M LLM input/output tokens and {money(usage.rates.embedding_per_million, cur)} per 1M embedding tokens — set
        LLM_PRICE_INPUT_PER_MILLION, LLM_PRICE_OUTPUT_PER_MILLION and EMBEDDING_PRICE_PER_MILLION to your contract.
        {usage.models.length > 0 ? ` Models: ${usage.models.join(", ")}.` : ""}
        {run.estimated || since.estimated ? " Some token counts were estimated (the provider didn’t report usage)." : ""}
      </p>
    </div>
  );
}
