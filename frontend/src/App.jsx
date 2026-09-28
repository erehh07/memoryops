import { useCallback, useEffect, useState } from "react";
import { api } from "./api";
import ExceptionDetail from "./ExceptionDetail.jsx";
import { ActionPill, Badge, Button, LearningChart, TYPE_LABEL, fmtSize } from "./ui.jsx";
import MemoryInspector from "./MemoryInspector.jsx";
import AuditLog from "./AuditLog.jsx";

export default function App() {
  const [status, setStatus] = useState(null);
  const [batches, setBatches] = useState([]);
  const [batchId, setBatchId] = useState(1);
  const [exceptions, setExceptions] = useState([]);
  const [selected, setSelected] = useState(null);
  const [tab, setTab] = useState("exceptions");
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [toast, setToast] = useState("");

  const refresh = useCallback(async () => {
    try {
      const [s, b, e] = await Promise.all([api.status(), api.batches(), api.exceptions(batchId)]);
      setStatus(s);
      setBatches(b);
      setExceptions(e);
      setError("");
    } catch (err) {
      setError(`Backend not reachable: ${err.message}`);
    }
  }, [batchId]);

  useEffect(() => { refresh(); }, [refresh]);

  const run = async (label, fn, done) => {
    setBusy(label);
    setError("");
    try {
      const r = await fn();
      if (done) setToast(done(r));
      await refresh();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy("");
    }
  };

  const batch = batches.find((b) => b.batch_id === batchId);
  const localMemory = status?.memory?.backend === "local";

  return (
    <div className="min-h-screen bg-slate-50 text-slate-800">

      {/* ── Header ── */}
      <header className="bg-white border-b border-slate-200">
        <div className="max-w-7xl mx-auto px-4 h-14 flex items-center gap-4">
          {/* Brand */}
          <div className="flex items-baseline gap-2 mr-auto">
            <span className="text-sm font-semibold tracking-tight text-slate-900">MemoryOps</span>
            <span className="hidden sm:inline text-xs text-slate-400">AP Exception Agent</span>
          </div>

          {/* Status dots */}
          {status && (
            <div className="flex items-center gap-3">
              <StatusDot
                ok={!localMemory}
                okLabel="Hindsight"
                failLabel="Local memory"
                detail={status.memory.detail}
              />
              <StatusDot
                ok={status.llm.mode === "groq"}
                okLabel="Groq"
                failLabel="Offline LLM"
                detail={status.llm.detail}
              />
            </div>
          )}

          <Button
            variant="ghost"
            disabled={!!busy}
            onClick={() => {
              if (confirm("Reset all data and wipe the agent's memory?"))
                run("reset", api.reset, () => "Data re-seeded and memory wiped.");
            }}
          >
            Reset demo
          </Button>
        </div>

        {/* Hindsight fallback notice */}
        {localMemory && (
          <div className="bg-amber-50 border-t border-amber-200 text-amber-900 text-xs">
            <div className="max-w-7xl mx-auto px-4 py-1.5 flex items-center gap-3">
              <span>Hindsight unavailable — {status.memory.detail}. Precedents stored in local fallback.</span>
              <button
                className="underline underline-offset-2"
                onClick={() => run("reconnect", api.reconnect, (s) => `Memory backend: ${s.backend}`)}
              >
                Retry
              </button>
            </div>
          </div>
        )}
      </header>

      {/* ── Body ── */}
      <main className="max-w-7xl mx-auto px-4 py-5 grid gap-5 lg:grid-cols-[280px_1fr]">

        {/* ── Sidebar ── */}
        <aside className="space-y-4">

          {/* Batch list */}
          <div className="bg-white border border-slate-200 rounded-md overflow-hidden">
            <div className="px-3 py-2.5 border-b border-slate-100">
              <p className="text-xs font-semibold text-slate-500 uppercase tracking-wider">Invoice Batches</p>
            </div>
            <div className="divide-y divide-slate-100">
              {batches.map((b) => {
                const active = b.batch_id === batchId;
                const done = b.status === "processed";
                const allDecided = done && b.decided === b.exceptions;
                return (
                  <button
                    key={b.batch_id}
                    onClick={() => { setBatchId(b.batch_id); setSelected(null); setTab("exceptions"); }}
                    className={`w-full text-left px-3 py-2.5 flex gap-2 items-start transition-colors
                      ${active ? "bg-indigo-50 border-l-2 border-l-indigo-500" : "border-l-2 border-l-transparent hover:bg-slate-50"}`}
                  >
                    <div className="flex-1 min-w-0">
                      <div className="text-sm font-medium text-slate-800 truncate">{b.name}</div>
                      <div className="text-xs text-slate-500 mt-0.5">
                        {done
                          ? `${b.exceptions} exceptions · ${b.decided}/${b.exceptions} decided`
                          : `${b.invoices} invoices · not processed`}
                      </div>
                    </div>
                    <span className={`text-xs font-medium mt-0.5 shrink-0 ${
                      allDecided ? "text-emerald-600" : done ? "text-amber-600" : "text-slate-400"
                    }`}>
                      {allDecided ? "Complete" : done ? "In review" : "New"}
                    </span>
                  </button>
                );
              })}
            </div>

            {/* Batch actions */}
            {batch && (
              <div className="px-3 py-2.5 border-t border-slate-100 space-y-2">
                {batch.status !== "processed" ? (
                  <Button className="w-full" disabled={!!busy}
                    onClick={() => run("process", () => api.process(batchId),
                      (r) => `Matched ${r.invoices} invoices · ${r.exceptions} exceptions · ${r.recommended} recommendations`)}>
                    {busy === "process" ? "Running match…" : "Run three-way match"}
                  </Button>
                ) : (
                  <>
                    <Button className="w-full" variant="secondary" disabled={!!busy || batch.decided === batch.exceptions}
                      onClick={() => run("replay", () => api.replay(batchId),
                        (r) => `Replayed ${r.applied} senior decisions` +
                          (r.no_senior_decision.length ? ` (${r.no_senior_decision.length} skipped)` : ""))}>
                      {busy === "replay" ? "Replaying…" : "Replay seniors' decisions"}
                    </Button>
                    <Button className="w-full" variant="ghost" disabled={!!busy || batch.decided === batch.exceptions}
                      onClick={() => run("rerun", () => api.rerun(batchId),
                        (r) => `Re-ran ${r.recommended} recommendations`)}>
                      {busy === "rerun" ? "Recalling…" : "Re-run open recommendations"}
                    </Button>
                  </>
                )}
              </div>
            )}
          </div>

          {/* Metrics */}
          <div className="bg-white border border-slate-200 rounded-md overflow-hidden">
            <div className="px-3 py-2.5 border-b border-slate-100">
              <p className="text-xs font-semibold text-slate-500 uppercase tracking-wider">Agent Learning</p>
              <p className="text-xs text-slate-400 mt-0.5">Escalation rate and human agreement per batch</p>
            </div>
            <div className="px-3 py-3">
              <LearningChart batches={batches} />
            </div>
          </div>
        </aside>

        {/* ── Main panel ── */}
        <section className="min-w-0">

          {/* Tab bar */}
          <div className="flex border-b border-slate-200 mb-4">
            {[
              ["exceptions", "Exceptions"],
              ["memory", "Memory inspector"],
              ["audit", "Audit log"],
            ].map(([k, label]) => (
              <button
                key={k}
                onClick={() => setTab(k)}
                className={`px-4 py-2.5 text-sm font-medium border-b-2 -mb-px transition-colors ${
                  tab === k
                    ? "border-indigo-600 text-indigo-700"
                    : "border-transparent text-slate-500 hover:text-slate-700 hover:border-slate-300"
                }`}
              >
                {label}
              </button>
            ))}
          </div>

          {/* Notifications */}
          {error && (
            <div className="mb-3 rounded border border-red-200 bg-red-50 text-red-800 text-sm px-3 py-2">
              {error}
            </div>
          )}
          {toast && (
            <div className="mb-3 rounded border border-emerald-200 bg-emerald-50 text-emerald-800 text-sm px-3 py-2 flex items-center">
              <span className="mr-auto">{toast}</span>
              <button onClick={() => setToast("")} className="text-emerald-600 ml-2 leading-none">×</button>
            </div>
          )}

          {tab === "exceptions" && (
            <div className={`grid gap-4 ${selected ? "xl:grid-cols-[minmax(0,1fr)_minmax(0,1.2fr)]" : ""}`}>
              <ExceptionTable
                batch={batch}
                exceptions={exceptions}
                selected={selected}
                onSelect={setSelected}
              />
              {selected && (
                <ExceptionDetail
                  key={selected}
                  id={selected}
                  onClose={() => setSelected(null)}
                  onChanged={refresh}
                />
              )}
            </div>
          )}
          {tab === "memory" && <MemoryInspector status={status} />}
          {tab === "audit"  && <AuditLog />}
        </section>
      </main>
    </div>
  );
}

/* ── Header status indicator ───────────────────────────────────────────── */
function StatusDot({ ok, okLabel, failLabel, detail }) {
  return (
    <span title={detail} className="inline-flex items-center gap-1.5 text-xs text-slate-500 cursor-default">
      <span className={`w-1.5 h-1.5 rounded-full ${ok ? "bg-emerald-500" : "bg-amber-400"}`} />
      {ok ? okLabel : failLabel}
    </span>
  );
}

/* ── Exception table ────────────────────────────────────────────────────── */
function ExceptionTable({ batch, exceptions, selected, onSelect }) {
  if (!batch) return null;
  if (batch.status !== "processed")
    return (
      <div className="bg-white border border-dashed border-slate-300 rounded-md p-10 text-center text-sm text-slate-500">
        {batch.name} · {batch.invoices} invoices pending.
        <br />
        <span className="text-slate-400">Run the three-way match to detect exceptions.</span>
      </div>
    );
  if (!exceptions.length)
    return (
      <div className="bg-white border border-slate-200 rounded-md p-6 text-sm text-slate-500">
        No exceptions in this batch.
      </div>
    );

  return (
    <div className="bg-white border border-slate-200 rounded-md overflow-x-auto">
      <table className="w-full text-sm border-collapse">
        <thead>
          <tr className="border-b border-slate-200 bg-slate-50">
            <th className="px-4 py-2.5 text-left text-xs font-semibold text-slate-500 uppercase tracking-wide">Invoice</th>
            <th className="px-4 py-2.5 text-left text-xs font-semibold text-slate-500 uppercase tracking-wide">Exception</th>
            <th className="px-4 py-2.5 text-left text-xs font-semibold text-slate-500 uppercase tracking-wide">Agent</th>
            <th className="px-4 py-2.5 text-left text-xs font-semibold text-slate-500 uppercase tracking-wide">Decision</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-100">
          {exceptions.map((e) => {
            const r = e.recommendation;
            const d = e.decision;
            const isSelected = selected === e.id;
            return (
              <tr
                key={e.id}
                onClick={() => onSelect(e.id)}
                className={`cursor-pointer transition-colors ${
                  isSelected ? "bg-indigo-50/70" : "hover:bg-slate-50"
                }`}
              >
                <td className="px-4 py-3">
                  <div className="font-medium text-slate-900 tabular-nums">{e.invoice.number}</div>
                  <div className="text-xs text-slate-500 mt-0.5 truncate max-w-[160px]">{e.vendor.name}</div>
                </td>
                <td className="px-4 py-3">
                  <div className="text-slate-700">{TYPE_LABEL[e.type]}</div>
                  <div className="text-xs text-slate-500 mt-0.5 tabular-nums">{fmtSize(e)}</div>
                </td>
                <td className="px-4 py-3">
                  {r ? (
                    <div>
                      <ActionPill action={r.action} />
                      <div className="text-xs text-slate-400 mt-0.5">
                        {r.cited_ids.length ? `${r.cited_ids.length} precedent${r.cited_ids.length !== 1 ? "s" : ""}` : "no precedent"}
                      </div>
                    </div>
                  ) : (
                    <span className="text-xs text-slate-400">—</span>
                  )}
                </td>
                <td className="px-4 py-3">
                  {d ? (
                    <div>
                      <ActionPill action={d.decision} />
                      {d.is_override && (
                        <div className="text-xs text-rose-600 mt-0.5">override</div>
                      )}
                    </div>
                  ) : (
                    <span className="text-xs text-slate-400">Pending</span>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
