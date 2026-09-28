import { useEffect, useState } from "react";
import { api } from "./api";
import { ActionPill, Badge, Button, TYPE_LABEL, fmtSize, money } from "./ui.jsx";

const ACTION_TO_DECISION = { approve: "approved", reject: "rejected", adjust: "adjusted", escalate: "escalated" };
const LAST_APPROVER = "memoryops.approver";

function loadApprover() {
  try { return JSON.parse(localStorage.getItem(LAST_APPROVER)) || { name: "", role: "" }; }
  catch { return { name: "", role: "" }; }
}

export default function ExceptionDetail({ id, onClose, onChanged }) {
  const [x, setX] = useState(null);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);

  const load = async () => {
    try { setX(await api.exception(id)); }
    catch (e) { setErr(e.message); }
  };
  useEffect(() => { load(); }, [id]);

  if (!x) return (
    <div className="bg-white border border-slate-200 rounded-md p-6 text-sm text-slate-500">
      {err || "Loading…"}
    </div>
  );
  const r = x.recommendation;

  const rerun = async () => {
    setBusy(true); setErr("");
    try { setX(await api.recommend(id)); onChanged(); }
    catch (e) { setErr(e.message); }
    finally { setBusy(false); }
  };

  return (
    <div className="bg-white border border-slate-200 rounded-md text-sm min-w-0 divide-y divide-slate-100">

      {/* ── Title row ── */}
      <div className="px-4 py-3 flex items-start gap-3">
        <div className="flex-1 min-w-0">
          <div className="text-xs text-slate-500 mb-0.5">{x.vendor.name} · {x.vendor.payment_terms} · {x.vendor.currency}</div>
          <div className="flex items-baseline gap-2">
            <span className="font-semibold text-slate-900 tabular-nums">{x.invoice.number}</span>
            <span className="text-slate-500">·</span>
            <span className="text-slate-700">{TYPE_LABEL[x.type]}</span>
          </div>
          <div className="text-xs text-slate-500 mt-0.5 tabular-nums">{fmtSize(x)}</div>
        </div>
        <button onClick={onClose} className="text-slate-400 hover:text-slate-700 text-xl leading-none mt-0.5">×</button>
      </div>

      {/* ── Three-way match ── */}
      <ThreeWayMatch x={x} />

      {/* ── Agent recommendation ── */}
      {r && (
        <div>
          <div className="px-4 py-2 bg-slate-50 flex flex-wrap items-center gap-3">
            <span className="text-xs font-semibold text-slate-500 uppercase tracking-wide">Agent recommendation</span>
            <ActionPill action={r.action} />
            {r.amount && <span className="tabular-nums text-slate-600 text-xs">{x.invoice.currency} {money(r.amount)}</span>}
            <span className="text-xs text-slate-400">confidence {Math.round(r.confidence * 100)}%</span>
            <span className="ml-auto text-xs text-slate-400" title={r.engine}>
              {r.engine.split(" | ")[0]} · {r.memory_backend}
            </span>
          </div>
          <div className="px-4 py-3 space-y-3">
            <p className="text-slate-700 leading-relaxed">{r.rationale}</p>
            {r.engine.includes(" | ") && (
              <p className="text-xs text-amber-700 bg-amber-50 px-2 py-1.5 rounded border border-amber-200">
                {r.engine.split(" | ").slice(1).join(" ")}
              </p>
            )}
            {r.suggested_approver && (
              <p className="text-xs text-slate-600">
                <span className="font-medium">Route to:</span> {r.suggested_approver} — all recalled decisions for this case came from this approver
              </p>
            )}
            <Precedents recalled={r.recalled} cited={r.cited_ids} />
            {x.status !== "decided" && (
              <Button variant="ghost" disabled={busy} onClick={rerun} className="text-xs">
                {busy ? "Recalling…" : "Re-run with current memory"}
              </Button>
            )}
          </div>
        </div>
      )}

      {/* ── Decision ── */}
      {x.decision
        ? <DecisionView d={x.decision} currency={x.invoice.currency} />
        : <DecisionForm x={x} onDone={(nx) => { setX(nx); onChanged(); }} />
      }
      {err && <div className="px-4 py-2 text-xs text-red-700 border-t border-slate-100">{err}</div>}
    </div>
  );
}

/* ── Three-way match table ─────────────────────────────────────────────── */
function ThreeWayMatch({ x }) {
  const poPrice = Object.fromEntries((x.po?.lines || []).map((l) => [l.sku, l]));
  const received = {};
  (x.receipts || []).forEach((g) => g.lines.forEach((l) => (received[l.sku] = (received[l.sku] || 0) + Number(l.qty_received))));

  return (
    <details open>
      <summary className="px-4 py-2.5 bg-slate-50 text-xs font-semibold text-slate-500 uppercase tracking-wide cursor-pointer select-none">
        Three-way match &nbsp;·&nbsp; PO {x.invoice.po_number || "none"} &nbsp;·&nbsp; {x.invoice.date}
        {x.receipts?.length ? ` · ${x.receipts.map((g) => g.gr_number).join(", ")}` : ""}
      </summary>
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="border-b border-slate-100 text-slate-500 text-left">
              <th className="px-4 py-2">Item</th>
              <th className="px-3 py-2 text-right">Inv qty</th>
              <th className="px-3 py-2 text-right">PO qty</th>
              <th className="px-3 py-2 text-right">Received</th>
              <th className="px-3 py-2 text-right">Inv price</th>
              <th className="px-4 py-2 text-right">PO price</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-50">
            {x.invoice.lines.map((l) => {
              const p = poPrice[l.sku];
              const priceOff = p && Number(p.unit_price) !== Number(l.unit_price);
              const qtyOff = p && received[l.sku] !== undefined && Number(received[l.sku]) !== Number(l.qty);
              return (
                <tr key={l.sku}>
                  <td className="px-4 py-2">
                    <div className="font-mono text-slate-700">{l.sku}</div>
                    <div className="text-slate-400">{l.description}</div>
                  </td>
                  <td className={`px-3 py-2 text-right tabular-nums ${qtyOff ? "text-rose-600 font-semibold" : "text-slate-700"}`}>{l.qty}</td>
                  <td className="px-3 py-2 text-right tabular-nums text-slate-500">{p ? p.qty : <span className="text-rose-600">—</span>}</td>
                  <td className={`px-3 py-2 text-right tabular-nums ${qtyOff ? "text-rose-600 font-semibold" : "text-slate-500"}`}>
                    {p ? received[l.sku] ?? 0 : "—"}
                  </td>
                  <td className={`px-3 py-2 text-right tabular-nums ${priceOff || !p ? "text-rose-600 font-semibold" : "text-slate-700"}`}>
                    {money(l.unit_price)}
                  </td>
                  <td className="px-4 py-2 text-right tabular-nums text-slate-500">{p ? money(p.unit_price) : "—"}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <div className="px-4 py-2 border-t border-slate-100 text-xs text-slate-500 flex flex-wrap gap-x-4">
        <span>Subtotal {money(x.invoice.subtotal)}</span>
        <span>Tax {money(x.invoice.tax)} ({x.vendor.tax_label} contracted {(Number(x.vendor.tax_rate) * 100).toFixed(2)}%)</span>
        <span className="font-medium text-slate-700">Total {x.invoice.currency} {money(x.invoice.total)}</span>
      </div>
    </details>
  );
}

/* ── Recalled precedents ───────────────────────────────────────────────── */
function Precedents({ recalled, cited }) {
  if (!recalled?.length) return <p className="text-xs text-slate-400">No memory returned for this query.</p>;
  const sorted = [...recalled].sort((a, b) => (cited.includes(b.id) - cited.includes(a.id)) || (b.relevant - a.relevant));
  return (
    <div>
      <div className="text-xs font-semibold text-slate-500 uppercase tracking-wide mb-2">
        Evidence recalled ({recalled.length}) · cited: {cited.length ? cited.join(", ") : "none"}
      </div>
      <div className="space-y-1.5">
        {sorted.map((p) => {
          const isCited = cited.includes(p.id);
          return (
            <div
              key={p.id}
              className={`rounded border px-3 py-2 text-xs ${
                isCited
                  ? "border-indigo-200 bg-indigo-50/60"
                  : p.relevant
                  ? "border-slate-200 bg-white"
                  : "border-dashed border-slate-200 text-slate-400"
              }`}
            >
              <div className="flex flex-wrap items-center gap-2 mb-1">
                <span className="font-mono font-semibold text-slate-700">{p.id}</span>
                <span className="text-slate-400">{p.kind.replace("_", " ")}</span>
                {isCited && <Badge tone="indigo">cited</Badge>}
                {!p.relevant && <span className="text-slate-400">different vendor or type</span>}
                <span className="ml-auto text-slate-400">{p.source} · {Number(p.score).toFixed(2)}</span>
              </div>
              <div className="leading-snug text-slate-600">{p.text}</div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

/* ── Decision form ─────────────────────────────────────────────────────── */
function DecisionForm({ x, onDone }) {
  const saved = loadApprover();
  const recoDecision = x.recommendation && x.recommendation.action !== "escalate"
    ? ACTION_TO_DECISION[x.recommendation.action] : "";
  const [decision, setDecision] = useState(recoDecision);
  const [name, setName] = useState(saved.name);
  const [role, setRole] = useState(saved.role);
  const [reason, setReason] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);

  const submit = async (ev) => {
    ev.preventDefault(); setBusy(true); setErr("");
    try {
      try { localStorage.setItem(LAST_APPROVER, JSON.stringify({ name, role })); } catch { /* fine */ }
      onDone(await api.decide(x.id, { decision, approver_name: name, approver_role: role, reason }));
    } catch (e) { setErr(e.message); }
    finally { setBusy(false); }
  };
  const override = x.recommendation && x.recommendation.action !== "escalate"
    && decision && decision !== recoDecision;

  return (
    <form onSubmit={submit} className="px-4 py-4 space-y-3">
      <div className="text-xs font-semibold text-slate-500 uppercase tracking-wide mb-1">
        Your decision
        <span className="normal-case font-normal text-slate-400 ml-2">— recorded only. Nothing is paid or posted.</span>
      </div>

      <div className="space-y-1">
        {x.options.map((o) => {
          const dv = ACTION_TO_DECISION[o.action];
          return (
            <label
              key={o.action}
              className={`flex items-center gap-2.5 rounded border px-3 py-2 cursor-pointer transition-colors ${
                decision === dv ? "border-indigo-400 bg-indigo-50/60" : "border-slate-200 hover:bg-slate-50"
              }`}
            >
              <input type="radio" name="decision" checked={decision === dv} onChange={() => setDecision(dv)} className="accent-indigo-600" />
              <ActionPill action={o.action} />
              <span className="flex-1 text-slate-700">{o.label}</span>
              {o.amount !== null && <span className="tabular-nums text-slate-500 text-xs">{x.invoice.currency} {money(o.amount)}</span>}
            </label>
          );
        })}
      </div>

      <div className="grid grid-cols-2 gap-2">
        <input
          className="border border-slate-300 rounded px-2.5 py-1.5 text-sm placeholder:text-slate-400 focus:outline-none focus:ring-1 focus:ring-indigo-400"
          placeholder="Approver name" value={name} onChange={(e) => setName(e.target.value)} required minLength={2}
        />
        <input
          className="border border-slate-300 rounded px-2.5 py-1.5 text-sm placeholder:text-slate-400 focus:outline-none focus:ring-1 focus:ring-indigo-400"
          placeholder="Role (e.g. AP Manager)" value={role} onChange={(e) => setRole(e.target.value)} required minLength={2}
        />
      </div>
      <textarea
        className="border border-slate-300 rounded px-2.5 py-1.5 text-sm w-full placeholder:text-slate-400 focus:outline-none focus:ring-1 focus:ring-indigo-400"
        rows={2}
        placeholder="Reason — this is what the agent will learn from"
        value={reason} onChange={(e) => setReason(e.target.value)}
        required minLength={5}
      />
      {override && (
        <p className="text-xs text-rose-600 bg-rose-50 border border-rose-200 rounded px-2.5 py-1.5">
          This overrides the agent's recommendation and will be recorded as an override.
        </p>
      )}
      <div className="flex items-center gap-2">
        <Button type="submit" disabled={busy || !decision}>
          {busy ? "Saving…" : "Record decision"}
        </Button>
        {err && <span className="text-xs text-red-700">{err}</span>}
      </div>
    </form>
  );
}

/* ── Decision view (already decided) ──────────────────────────────────── */
function DecisionView({ d, currency }) {
  return (
    <div className="px-4 py-3 bg-emerald-50/40 space-y-1">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-xs font-semibold text-slate-500 uppercase tracking-wide">Decision</span>
        <ActionPill action={d.decision} />
        {d.amount && <span className="text-slate-600 text-xs tabular-nums">{currency} {money(d.amount)}</span>}
        {d.is_override && <Badge tone="rose">override</Badge>}
        {d.source === "replay" && <Badge tone="slate">senior replay</Badge>}
      </div>
      <p className="text-slate-700 italic">"{d.reason}"</p>
      <p className="text-xs text-slate-500">
        {d.approver_name} ({d.approver_role}) · {d.decided_on} · record {d.memory_record_id}
        {" "}{d.retained ? "" : <span className="text-rose-600">NOT retained</span>}
      </p>
    </div>
  );
}
