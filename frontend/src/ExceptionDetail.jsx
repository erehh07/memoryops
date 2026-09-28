import { useEffect, useState } from "react";
import { api } from "./api";
import { ActionPill, Badge, Button, TYPE_LABEL, fmtSize, money } from "./ui.jsx";

const ACTION_TO_DECISION = { approve: "approved", reject: "rejected", adjust: "adjusted", escalate: "escalated" };
const LAST_APPROVER = "memoryops.approver";

function loadApprover() {
  try {
    return JSON.parse(localStorage.getItem(LAST_APPROVER)) || { name: "", role: "" };
  } catch {
    return { name: "", role: "" };
  }
}

export default function ExceptionDetail({ id, onClose, onChanged }) {
  const [x, setX] = useState(null);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);

  const load = async () => {
    try {
      setX(await api.exception(id));
    } catch (e) {
      setErr(e.message);
    }
  };
  useEffect(() => {
    load();
  }, [id]);

  if (!x) return <div className="bg-white rounded-lg border p-4 text-sm text-slate-500">{err || "Loading…"}</div>;
  const r = x.recommendation;

  const rerun = async () => {
    setBusy(true);
    setErr("");
    try {
      setX(await api.recommend(id));
      onChanged();
    } catch (e) {
      setErr(e.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="bg-white rounded-lg border border-slate-200 p-4 space-y-4 text-sm min-w-0">
      <div className="flex items-start gap-2">
        <div className="mr-auto">
          <div className="text-xs text-slate-500">{x.vendor.name} · {x.vendor.payment_terms} · {x.vendor.currency}</div>
          <h3 className="text-base font-semibold">
            {x.invoice.number} · {TYPE_LABEL[x.type]}
          </h3>
          <div className="text-slate-600">{fmtSize(x)}</div>
        </div>
        <button onClick={onClose} className="text-slate-400 hover:text-slate-700 text-lg leading-none">×</button>
      </div>

      <Documents x={x} />

      {r && (
        <section className="rounded-md border border-slate-200">
          <div className="flex flex-wrap items-center gap-2 px-3 py-2 bg-slate-50 border-b border-slate-200">
            <span className="font-medium">Agent recommendation</span>
            <ActionPill action={r.action} />
            {r.amount && (
              <span className="text-slate-600">
                {x.invoice.currency} {money(r.amount)}
              </span>
            )}
            <span className="text-xs text-slate-500">confidence {Math.round(r.confidence * 100)}%</span>
            <span className="ml-auto text-xs text-slate-400" title={r.engine}>
              {r.engine.split(" | ")[0]} · memory: {r.memory_backend}
            </span>
          </div>
          <div className="p-3 space-y-3">
            <p className="leading-relaxed">{r.rationale}</p>
            {r.engine.includes(" | ") && <p className="text-xs text-amber-700">{r.engine.split(" | ").slice(1).join(" ")}</p>}
            {r.suggested_approver && (
              <p className="text-xs">
                <Badge tone="indigo">Route to {r.suggested_approver}</Badge>{" "}
                <span className="text-slate-500">every recalled decision for this case came from this approver</span>
              </p>
            )}
            <Precedents recalled={r.recalled} cited={r.cited_ids} />
            {x.status !== "decided" && (
              <Button variant="ghost" disabled={busy} onClick={rerun}>
                {busy ? "Recalling…" : "Re-run with current memory"}
              </Button>
            )}
          </div>
        </section>
      )}

      {x.decision ? <DecisionView d={x.decision} currency={x.invoice.currency} /> : (
        <DecisionForm x={x} onDone={(nx) => { setX(nx); onChanged(); }} />
      )}
      {err && <p className="text-red-700 text-xs">{err}</p>}
    </div>
  );
}

function Documents({ x }) {
  const poPrice = Object.fromEntries((x.po?.lines || []).map((l) => [l.sku, l]));
  const received = {};
  (x.receipts || []).forEach((g) => g.lines.forEach((l) => (received[l.sku] = (received[l.sku] || 0) + Number(l.qty_received))));
  return (
    <details className="rounded-md border border-slate-200" open>
      <summary className="cursor-pointer px-3 py-2 bg-slate-50 text-xs font-medium text-slate-600">
        Three-way match · PO {x.invoice.po_number || "none"} · invoice {x.invoice.date}
        {x.receipts?.length ? ` · ${x.receipts.map((g) => g.gr_number).join(", ")}` : ""}
      </summary>
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead className="text-slate-500 text-left">
            <tr>
              <th className="px-3 py-1.5">Item</th>
              <th className="px-2 py-1.5 text-right">Inv qty</th>
              <th className="px-2 py-1.5 text-right">PO qty</th>
              <th className="px-2 py-1.5 text-right">Received</th>
              <th className="px-2 py-1.5 text-right">Inv price</th>
              <th className="px-3 py-1.5 text-right">PO price</th>
            </tr>
          </thead>
          <tbody>
            {x.invoice.lines.map((l) => {
              const p = poPrice[l.sku];
              const priceOff = p && Number(p.unit_price) !== Number(l.unit_price);
              const qtyOff = p && received[l.sku] !== undefined && Number(received[l.sku]) !== Number(l.qty);
              return (
                <tr key={l.sku} className="border-t border-slate-100">
                  <td className="px-3 py-1.5">
                    <div className="font-mono">{l.sku}</div>
                    <div className="text-slate-500">{l.description}</div>
                  </td>
                  <td className={`px-2 text-right tabular-nums ${qtyOff ? "text-rose-600 font-semibold" : ""}`}>{l.qty}</td>
                  <td className="px-2 text-right tabular-nums">{p ? p.qty : <span className="text-rose-600">not on PO</span>}</td>
                  <td className={`px-2 text-right tabular-nums ${qtyOff ? "text-rose-600 font-semibold" : ""}`}>
                    {p ? received[l.sku] ?? 0 : "—"}
                  </td>
                  <td className={`px-2 text-right tabular-nums ${priceOff || !p ? "text-rose-600 font-semibold" : ""}`}>
                    {money(l.unit_price)}
                  </td>
                  <td className="px-3 text-right tabular-nums">{p ? money(p.unit_price) : "—"}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <div className="px-3 py-2 border-t border-slate-100 text-xs text-slate-600 flex flex-wrap gap-x-4">
        <span>Subtotal {money(x.invoice.subtotal)}</span>
        <span>Tax {money(x.invoice.tax)} ({x.vendor.tax_label} contracted {(Number(x.vendor.tax_rate) * 100).toFixed(2)}%)</span>
        <span className="font-medium">Total {x.invoice.currency} {money(x.invoice.total)}</span>
      </div>
    </details>
  );
}

function Precedents({ recalled, cited }) {
  if (!recalled?.length) return <p className="text-xs text-slate-500">Memory returned nothing for this query.</p>;
  const sorted = [...recalled].sort((a, b) => (cited.includes(b.id) - cited.includes(a.id)) || (b.relevant - a.relevant));
  return (
    <div>
      <div className="text-xs font-medium text-slate-600 mb-1">
        Recalled from memory ({recalled.length}) · cited: {cited.length ? cited.join(", ") : "none"}
      </div>
      <ul className="space-y-1.5">
        {sorted.map((p) => {
          const isCited = cited.includes(p.id);
          return (
            <li
              key={p.id}
              className={`rounded border px-2 py-1.5 text-xs ${
                isCited ? "border-indigo-300 bg-indigo-50" : p.relevant ? "border-slate-200" : "border-dashed border-slate-200 text-slate-400"
              }`}
            >
              <div className="flex flex-wrap gap-2 items-center mb-0.5">
                <span className="font-mono font-semibold">{p.id}</span>
                <span className="text-slate-500">{p.kind.replace("_", " ")}</span>
                {isCited && <Badge tone="indigo">cited</Badge>}
                {!p.relevant && <span>not used: different vendor or exception type</span>}
                <span className="ml-auto text-slate-400">{p.source} · score {Number(p.score).toFixed(2)}</span>
              </div>
              <div className="leading-snug">{p.text}</div>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

function DecisionForm({ x, onDone }) {
  const saved = loadApprover();
  const recoDecision = x.recommendation && x.recommendation.action !== "escalate" ? ACTION_TO_DECISION[x.recommendation.action] : "";
  const [decision, setDecision] = useState(recoDecision);
  const [name, setName] = useState(saved.name);
  const [role, setRole] = useState(saved.role);
  const [reason, setReason] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);

  const submit = async (ev) => {
    ev.preventDefault();
    setBusy(true);
    setErr("");
    try {
      try {
        localStorage.setItem(LAST_APPROVER, JSON.stringify({ name, role }));
      } catch {
        /* storage unavailable: fine */
      }
      onDone(await api.decide(x.id, { decision, approver_name: name, approver_role: role, reason }));
    } catch (e) {
      setErr(e.message);
    } finally {
      setBusy(false);
    }
  };
  const override = x.recommendation && x.recommendation.action !== "escalate" && decision && decision !== recoDecision;

  return (
    <form onSubmit={submit} className="rounded-md border border-slate-300 p-3 space-y-2">
      <div className="font-medium">Your decision <span className="text-xs text-slate-500 font-normal">(recorded only — nothing is paid or posted)</span></div>
      <div className="grid gap-1.5">
        {x.options.map((o) => {
          const dv = ACTION_TO_DECISION[o.action];
          return (
            <label key={o.action} className={`flex items-center gap-2 rounded border px-2 py-1.5 cursor-pointer ${decision === dv ? "border-indigo-400 bg-indigo-50" : "border-slate-200"}`}>
              <input type="radio" name="decision" checked={decision === dv} onChange={() => setDecision(dv)} />
              <ActionPill action={o.action} />
              <span className="flex-1">{o.label}</span>
              {o.amount !== null && <span className="tabular-nums text-slate-600">{x.invoice.currency} {money(o.amount)}</span>}
            </label>
          );
        })}
      </div>
      <div className="grid grid-cols-2 gap-2">
        <input className="border rounded px-2 py-1.5" placeholder="Approver name" value={name} onChange={(e) => setName(e.target.value)} required minLength={2} />
        <input className="border rounded px-2 py-1.5" placeholder="Role (e.g. AP Manager)" value={role} onChange={(e) => setRole(e.target.value)} required minLength={2} />
      </div>
      <textarea
        className="border rounded px-2 py-1.5 w-full"
        rows={2}
        placeholder="Reason — this is what the agent will learn from"
        value={reason}
        onChange={(e) => setReason(e.target.value)}
        required
        minLength={5}
      />
      {override && <p className="text-xs text-rose-700">This overrides the agent's recommendation and will be remembered as an override.</p>}
      <div className="flex items-center gap-2">
        <Button type="submit" disabled={busy || !decision}>{busy ? "Saving & retaining…" : "Record decision"}</Button>
        {err && <span className="text-xs text-red-700">{err}</span>}
      </div>
    </form>
  );
}

function DecisionView({ d, currency }) {
  return (
    <section className="rounded-md border border-emerald-200 bg-emerald-50/50 p-3 space-y-1">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-medium">Decision</span>
        <ActionPill action={d.decision} />
        {d.amount && <span className="text-slate-600">{currency} {money(d.amount)}</span>}
        {d.is_override && <Badge tone="rose">override of agent</Badge>}
        {d.source === "replay" && <Badge tone="slate">senior replay</Badge>}
      </div>
      <p>“{d.reason}”</p>
      <p className="text-xs text-slate-500">
        {d.approver_name} ({d.approver_role}) · {d.decided_on} · memory record {d.memory_record_id}{" "}
        {d.retained ? "retained" : <span className="text-rose-600">NOT retained (memory write failed)</span>}
      </p>
    </section>
  );
}
