"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { api, AssessmentListItem, Health, llmLabel } from "@/lib/api";
import { StatusBadge } from "@/components/StatusBadge";
import { RuntimeTimer } from "@/components/RuntimeTimer";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { ErrorBanner } from "@/components/ErrorBanner";
import { isInFlight, isTerminal } from "@/lib/status";

export default function HomePage() {
  const router = useRouter();
  const [items, setItems] = useState<AssessmentListItem[]>([]);
  const [health, setHealth] = useState<Health | null>(null);
  const [name, setName] = useState("Contoso Migration Discovery");
  const [files, setFiles] = useState<FileList | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [draftName, setDraftName] = useState("");
  const [busyId, setBusyId] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [sortBy, setSortBy] = useState<"updated" | "name" | "status">("updated");
  const [pendingDelete, setPendingDelete] = useState<AssessmentListItem | null>(null);

  const refresh = async () => {
    try {
      const [list, h] = await Promise.all([api.listAssessments(), api.health()]);
      setItems(list);
      setHealth(h);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load assessments");
    }
  };

  useEffect(() => {
    refresh();
  }, []);

  const hasRunning = items.some((item) => !isTerminal(item.status));

  useEffect(() => {
    if (!hasRunning) return;
    const timer = setInterval(() => {
      void refresh();
    }, 4000);
    return () => clearInterval(timer);
  }, [hasRunning]);

  const onSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!files?.length) {
      setError("Select at least one PDF, DOCX, XLSX, or ZIP file");
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const created = await api.createAssessment(name, Array.from(files));
      router.push(`/assessments/${created.id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Create failed");
      setLoading(false);
    }
  };

  const saveRename = async (id: string) => {
    const next = draftName.trim();
    if (!next) return;
    setBusyId(id);
    setError(null);
    try {
      await api.updateAssessment(id, next);
      setEditingId(null);
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Rename failed");
    } finally {
      setBusyId(null);
    }
  };

  const deleteItem = async (item: AssessmentListItem) => {
    setBusyId(item.id);
    setError(null);
    try {
      await api.deleteAssessment(item.id);
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Delete failed");
    } finally {
      setBusyId(null);
    }
  };

  const visibleItems = useMemo(() => {
    const q = search.trim().toLowerCase();
    const filtered = q ? items.filter((i) => i.name.toLowerCase().includes(q)) : items;
    const sorted = [...filtered];
    if (sortBy === "name") sorted.sort((a, b) => a.name.localeCompare(b.name));
    else if (sortBy === "status") sorted.sort((a, b) => a.status.localeCompare(b.status));
    else sorted.sort((a, b) => b.updated_at.localeCompare(a.updated_at));
    return sorted;
  }, [items, search, sortBy]);

  return (
    <div className="space-y-8">
      <section className="card p-8">
        <div className="mb-2 sans text-xs font-semibold uppercase tracking-wider text-[var(--accent)]">
          New assessment
        </div>
        <h1 className="mb-2 text-3xl">Start a migration readiness assessment</h1>
        <p className="sans mb-6 max-w-2xl text-sm text-[var(--muted)]">
          Upload architecture documents, CMDB/inventory exports, and questionnaires.
          The accelerator extracts source-linked facts, reconciles conflicts, builds a
          dependency graph, and produces a migration-ready report.
        </p>

        <form onSubmit={onSubmit} className="sans space-y-4">
          <div>
            <label className="mb-1 block text-sm font-medium">Assessment name</label>
            <input
              className="input"
              value={name}
              onChange={(e) => setName(e.target.value)}
              required
            />
          </div>
          <div>
            <label className="mb-1 block text-sm font-medium">
              Source documents (PDF, DOCX, XLSX, ZIP code snapshot)
            </label>
            <input
              className="input"
              type="file"
              multiple
              accept=".pdf,.docx,.doc,.xlsx,.xlsm,.txt,.zip,.md"
              onChange={(e) => setFiles(e.target.files)}
            />
            <p className="mt-1 text-xs text-[var(--muted)]">
              Include architecture/inventory/questionnaires, requirements/NFR docs, and optional
              application ZIP (package.json, pom.xml, Dockerfile, etc.). Max 50MB per file.
            </p>
          </div>
          {error && <ErrorBanner message={error} onDismiss={() => setError(null)} />}
          <button className="btn btn-primary" disabled={loading} type="submit">
            {loading ? "Starting pipeline…" : "Create & run assessment"}
          </button>
        </form>

        {health && (
          <div className="mt-6 flex flex-wrap gap-3 text-xs text-[var(--muted)]">
            <span>API: {health.status}</span>
            <span>LLM: {llmLabel(health)}</span>
            <span>RAG: {health.rag ? "on" : "off"}</span>
            <span>Embeddings: {health.embeddings}</span>
            <span>Search: {health.azure_search ? "Azure AI Search" : "local"}</span>
            <span>DB: {health.database}</span>
          </div>
        )}
      </section>

      <section>
        <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
          <h2 className="text-xl">
            Recent assessments
            {items.length ? ` (${visibleItems.length}/${items.length})` : ""}
          </h2>
          <div className="sans flex flex-wrap items-center gap-2 text-sm">
            <input
              className="input"
              type="search"
              placeholder="Search by name…"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
            <select
              className="input"
              value={sortBy}
              onChange={(e) => setSortBy(e.target.value as typeof sortBy)}
            >
              <option value="updated">Sort: recently updated</option>
              <option value="name">Sort: name</option>
              <option value="status">Sort: status</option>
            </select>
            <button className="btn btn-secondary" onClick={refresh} type="button">
              Refresh
            </button>
          </div>
        </div>
        <div className="card overflow-hidden">
          <table className="sans w-full text-left text-sm">
            <thead className="bg-[#faf9f7] text-xs uppercase text-[var(--muted)]">
              <tr>
                <th className="px-4 py-3">Name</th>
                <th className="px-4 py-3">Status</th>
                <th className="px-4 py-3">Runtime</th>
                <th className="px-4 py-3">Stage</th>
                <th className="px-4 py-3">Docs</th>
                <th className="px-4 py-3">Updated</th>
                <th className="px-4 py-3">Actions</th>
              </tr>
            </thead>
            <tbody>
              {items.length === 0 && (
                <tr>
                  <td className="px-4 py-6 text-[var(--muted)]" colSpan={7}>
                    No assessments yet. Upload sample-data files to begin.
                  </td>
                </tr>
              )}
              {items.length > 0 && visibleItems.length === 0 && (
                <tr>
                  <td className="px-4 py-6 text-[var(--muted)]" colSpan={7}>
                    No assessments match your search.
                  </td>
                </tr>
              )}
              {visibleItems.map((item) => (
                <tr key={item.id} className="border-t border-[var(--border)] hover:bg-[#faf9f7]">
                  <td className="px-4 py-3 font-medium">
                    {editingId === item.id ? (
                      <input
                        className="input"
                        value={draftName}
                        onChange={(e) => setDraftName(e.target.value)}
                        onKeyDown={(e) => {
                          if (e.key === "Enter") void saveRename(item.id);
                          if (e.key === "Escape") setEditingId(null);
                        }}
                        autoFocus
                      />
                    ) : (
                      <Link
                        href={`/assessments/${item.id}`}
                        className="text-[var(--foreground)] hover:text-[var(--accent)]"
                      >
                        {item.name}
                      </Link>
                    )}
                  </td>
                  <td className="px-4 py-3">
                    <StatusBadge status={item.status} />
                  </td>
                  <td className="px-4 py-3">
                    {item.runtime_seconds == null ? (
                      <span className="text-[var(--muted)]">—</span>
                    ) : (
                      <RuntimeTimer
                        key={item.pipeline_started_at ?? "not-started"}
                        compact
                        runtimeSeconds={item.runtime_seconds}
                        status={item.status}
                        running={!isTerminal(item.status) && !item.pipeline_finished_at}
                      />
                    )}
                  </td>
                  <td className="px-4 py-3 capitalize">
                    {item.workflow_stage.replaceAll("_", " ")}
                  </td>
                  <td className="px-4 py-3">{item.document_count}</td>
                  <td className="px-4 py-3 text-[var(--muted)]">
                    {new Date(item.updated_at).toLocaleString()}
                  </td>
                  <td className="px-4 py-3">
                    <div className="flex flex-wrap gap-2">
                      {editingId === item.id ? (
                        <>
                          <button
                            type="button"
                            className="btn btn-primary"
                            disabled={busyId === item.id}
                            onClick={() => void saveRename(item.id)}
                          >
                            Save
                          </button>
                          <button
                            type="button"
                            className="btn btn-secondary"
                            onClick={() => setEditingId(null)}
                          >
                            Cancel
                          </button>
                        </>
                      ) : (
                        <button
                          type="button"
                          className="btn btn-secondary"
                          disabled={busyId === item.id}
                          onClick={() => {
                            setEditingId(item.id);
                            setDraftName(item.name);
                          }}
                        >
                          Rename
                        </button>
                      )}
                      <button
                        type="button"
                        className="btn btn-secondary"
                        disabled={busyId === item.id || isInFlight(item.status)}
                        onClick={() => setPendingDelete(item)}
                      >
                        Delete
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
      <ConfirmDialog
        open={pendingDelete !== null}
        title="Delete assessment"
        message={`Delete "${pendingDelete?.name}"? This cannot be undone.`}
        confirmLabel="Delete"
        danger
        onCancel={() => setPendingDelete(null)}
        onConfirm={() => {
          const item = pendingDelete;
          setPendingDelete(null);
          if (item) void deleteItem(item);
        }}
      />
    </div>
  );
}
