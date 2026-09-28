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

  useEffect(() => {
    refresh();
  }, [refresh]);

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
    <div className="min-h-screen text-slate-800">
      <header className="bg-white border-b border-slate-200">
        <div className="max-w-7xl mx-auto px-4 py-3 flex flex-wrap items-center gap-3">
          <div className="mr-auto">
            <h1 className="text-xl font-semibold tracking-tight">
              MemoryOps <span className="text-slate-400 font-normal">· AP exception agent</span>
            </h1>
            <p className="text-xs text-slate-500">
              Recommends only. A human approves every decision. Nothing is paid or posted.
            </p>
          </div>
          {status && (
            <>
              <Badge
                tone={localMemory ? "amber" : "emerald"}
                title={status.memory.detail}
              >
                {localMemory ? "Memory: local fallback" : "Memory: Hindsight"}
              </Badge>
              <Badge tone={status.llm.mode === "groq" ? "emerald" : "amber"} title={status.llm.detail}>
                {status.llm.mode === "groq" ? `LLM: ${status.llm.models[0]}` : "LLM: offline (deterministic)"}
              </Badge>
            </>
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
        {localMemory && (
          <div className="bg-amber-50 border-t border-amber-200 text-amber-900 text-xs">
            <div className="max-w-7xl mx-auto px-4 py-1.5 flex items-center gap-3">
              <span>
                Hindsight is not in use ({status.memory.detail}). Precedents are stored in the local fallback store.
              </span>
              <button
                className="underline"
                onClick={() => run("reconnect", api.reconnect, (s) => `Memory backend: ${s.backend}`)}
              >
                Retry Hindsight
              </button>
            </div>
          </div>
        )}
      </header>

      <main className="max-w-7xl mx-auto px-4 py-4 grid gap-4 lg:grid-cols-[320px_1fr]">
        <aside className="space-y-4">
          <section className="bg-white rounded-lg border border-slate-200 p-3">
            <h2 className="text-sm font-semibold mb-2">Invoice batches</h2>
            <div className="space-y-2">
              {batches.map((b) => (
                <button
                  key={b.batch_id}
                  onClick={() => {
                    setBatchId(b.batch_id);
                    setSelected(null);
                    setTab("exceptions");
                  }}
                  className={`w-full text-left rounded-md border px-3 py-2 transition ${
                    b.batch_id === batchId ? "border-indigo-400 bg-indigo-50" : "border-slate-200 hover:bg-slate-50"
                  }`}
                >
                  <div className="flex justify-between text-sm font-medium">
                    <span>{b.name}</span>
                    <span className={b.status === "processed" ? "text-emerald-600" : "text-slate-400"}>
                      {b.status === "processed" ? "processed" : "new"}
                    </span>
                  </div>
                  <div className="text-xs text-slate-500 mt-0.5">
                    {b.invoices} invoices
                    {b.status === "processed" && (
                      <>
                        {" "}· {b.exceptions} exceptions · {b.decided}/{b.exceptions} decided
                      </>
                    )}
                  </div>
                </button>
              ))}
            </div>
            {batch && (
              <div className="mt-3 space-y-2">
                {batch.status !== "processed" ? (
                  <Button
                    className="w-full"
                    disabled={!!busy}
                    onClick={() =>
                      run(
                        "process",
                        () => api.process(batchId),
                        (r) => `Matched ${r.invoices} invoices, found ${r.exceptions} exceptions, recommended ${r.recommended}.`
                      )
                    }
                  >
                    {busy === "process" ? "Matching & recommending…" : "Run three-way match"}
                  </Button>
                ) : (
                  <>
                    <Button
                      className="w-full"
                      variant="secondary"
                      disabled={!!busy || batch.decided === batch.exceptions}
                      onClick={() =>
                        run(
                          "replay",
                          () => api.replay(batchId),
                          (r) =>
                            `Replayed ${r.applied} senior decisions into memory` +
                            (r.no_senior_decision.length ? ` (${r.no_senior_decision.length} have no senior decision)` : "")
                        )
                      }
                    >
                      {busy === "replay" ? "Teaching the agent…" : "Replay seniors' decisions"}
                    </Button>
                    <Button
                      className="w-full"
                      variant="ghost"
                      disabled={!!busy || batch.decided === batch.exceptions}
                      onClick={() => run("rerun", () => api.rerun(batchId), (r) => `Re-ran ${r.recommended} recommendations with current memory.`)}
                    >
                      {busy === "rerun" ? "Recalling & recommending…" : "Re-run open recommendations"}
                    </Button>
                  </>
                )}
              </div>
            )}
          </section>

          <section className="bg-white rounded-lg border border-slate-200 p-3">
            <h2 className="text-sm font-semibold">Is it learning?</h2>
            <p className="text-xs text-slate-500 mb-2">
              Share of exceptions the agent escalated when the batch arrived, and how often humans agreed with its
              non-escalated recommendations.
            </p>
            <LearningChart batches={batches} />
          </section>
        </aside>

        <section className="min-w-0">
          <nav className="flex flex-wrap gap-1 mb-3">
            {[
              ["exceptions", "Exceptions"],
              ["memory", "Memory inspector"],
              ["audit", "Audit log"],
            ].map(([k, label]) => (
              <button
                key={k}
                onClick={() => setTab(k)}
                className={`px-3 py-1.5 text-sm rounded-md ${
                  tab === k ? "bg-slate-800 text-white" : "text-slate-600 hover:bg-slate-200"
                }`}
              >
                {label}
              </button>
            ))}
          </nav>

          {error && <div className="mb-3 rounded-md bg-red-50 border border-red-200 text-red-800 text-sm px-3 py-2">{error}</div>}
          {toast && (
            <div className="mb-3 rounded-md bg-emerald-50 border border-emerald-200 text-emerald-800 text-sm px-3 py-2 flex">
              <span className="mr-auto">{toast}</span>
              <button onClick={() => setToast("")} className="text-emerald-600">×</button>
            </div>
          )}

          {tab === "exceptions" && (
            <div className={`grid gap-4 ${selected ? "xl:grid-cols-[minmax(0,1fr)_minmax(0,1.15fr)]" : ""}`}>
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
          {tab === "audit" && <AuditLog />}
        </section>
      </main>
    </div>
  );
}

function ExceptionTable({ batch, exceptions, selected, onSelect }) {
  if (!batch) return null;
  if (batch.status !== "processed")
    return (
      <div className="bg-white rounded-lg border border-dashed border-slate-300 p-8 text-center text-sm text-slate-500">
        {batch.name} has {batch.invoices} invoices waiting. Run the three-way match to find exceptions.
      </div>
    );
  if (!exceptions.length)
    return <div className="bg-white rounded-lg border p-6 text-sm text-slate-500">No exceptions in this batch.</div>;
  return (
    <div className="bg-white rounded-lg border border-slate-200 overflow-x-auto">
      <table className="w-full text-sm">
        <thead className="bg-slate-50 text-xs text-slate-500 text-left">
          <tr>
            <th className="px-3 py-2">Invoice</th>
            <th className="px-3 py-2">Exception</th>
            <th className="px-3 py-2">Agent</th>
            <th className="px-3 py-2">Human decision</th>
          </tr>
        </thead>
        <tbody>
          {exceptions.map((e) => {
            const r = e.recommendation;
            const d = e.decision;
            return (
              <tr
                key={e.id}
                onClick={() => onSelect(e.id)}
                className={`border-t border-slate-100 cursor-pointer ${
                  selected === e.id ? "bg-indigo-50" : "hover:bg-slate-50"
                }`}
              >
                <td className="px-3 py-2">
                  <div className="font-medium">{e.invoice.number}</div>
                  <div className="text-xs text-slate-500 truncate max-w-[180px]">{e.vendor.name}</div>
                </td>
                <td className="px-3 py-2">
                  <div>{TYPE_LABEL[e.type]}</div>
                  <div className="text-xs text-slate-500">{fmtSize(e)}</div>
                </td>
                <td className="px-3 py-2">
                  {r ? (
                    <div className="flex items-center gap-2">
                      <ActionPill action={r.action} />
                      <span className="text-xs text-slate-500">
                        {r.cited_ids.length ? `${r.cited_ids.length} cited` : "no precedent"}
                      </span>
                    </div>
                  ) : (
                    <span className="text-xs text-slate-400">—</span>
                  )}
                </td>
                <td className="px-3 py-2">
                  {d ? (
                    <div className="flex items-center gap-2">
                      <ActionPill action={d.decision} />
                      {d.is_override && <span className="text-xs text-rose-600">override</span>}
                    </div>
                  ) : (
                    <span className="text-xs text-amber-600">awaiting approver</span>
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
